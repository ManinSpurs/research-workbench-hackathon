"""The capability gate reads its own declared table; it never whitelists one id.

These tests pin the contract that ``app/packs.require_text_binding`` decides from
``CAPABILITIES`` (the declared status table) instead of a hard-coded id. No
provider, SDK or network path is exercised: this is configuration honesty only.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import packs
from app.harness import Conflict
from test_dot_safety import h, StubAdapter  # noqa: F401  (fixture re-export)


def _declared(capability_id):
    return next(item for item in packs.CAPABILITIES if item["id"] == capability_id)


def test_mock_text_is_executable():
    assert packs.require_text_binding("mock_text") == "mock_text"
    assert packs.capability_executable("mock_text") is True
    assert _declared("mock_text")["status"] == "local_mock_only"


def test_meta_text_is_refused_and_message_names_capability_and_status():
    # meta_text is a live paid provider: enabling it is a separate decision with
    # its own consent flow in app/ai/service.py, so this change must not enable it.
    assert _declared("meta_text")["status"] == "blocked"
    assert packs.capability_executable("meta_text") is False
    with pytest.raises(ValueError) as excinfo:
        packs.require_text_binding("meta_text")
    message = str(excinfo.value)
    assert "meta_text" in message
    assert "blocked" in message


def test_undeclared_capability_is_refused_by_name():
    with pytest.raises(ValueError) as excinfo:
        packs.require_text_binding("no_such_capability")
    message = str(excinfo.value)
    assert "no_such_capability" in message
    assert "CAPABILITIES" in message


def test_every_declared_capability_either_executes_or_raises():
    # No id may be silently ignored: each one either executes or refuses loudly.
    executed, refused = [], []
    for item in packs.CAPABILITIES:
        try:
            returned = packs.require_text_binding(item["id"])
        except ValueError as exc:
            assert item["id"] in str(exc), item["id"]
            assert item["status"] in str(exc), item["id"]
            refused.append(item["id"])
        else:
            assert returned == item["id"]
            executed.append(item["id"])
    assert executed == ["mock_text"]
    assert sorted(executed + refused) == sorted(item["id"] for item in packs.CAPABILITIES)
    assert packs.executable_capabilities() == ("mock_text",)


def test_gate_table_and_declared_table_agree():
    declared_ids = {item["id"] for item in packs.CAPABILITIES}
    gated_ids = set(packs.CAPABILITY_GATE)
    assert gated_ids <= declared_ids, gated_ids - declared_ids
    for capability_id, gate in packs.CAPABILITY_GATE.items():
        assert isinstance(gate["executable"], bool), capability_id
        assert gate["reason"] and gate["enabled_by"], capability_id
    assert all(not gate["executable"] for cid, gate in packs.CAPABILITY_GATE.items()
               if cid != "mock_text")


def test_added_capability_with_non_executable_status_is_refused(monkeypatch):
    # Adding a capability to the declared table must never make it executable:
    # the entry carries a non-executable status and the gate is not edited.
    gate_source = packs.CAPABILITY_GATE
    added = {"id": "speech_to_text_v2", "kind": "audio_to_text",
             "status": "not_implemented", "usage_unit": "audio_duration"}
    monkeypatch.setattr(packs, "CAPABILITIES", packs.CAPABILITIES + (added,))
    assert packs.CAPABILITY_GATE is gate_source  # gate function/table untouched
    with pytest.raises(ValueError) as excinfo:
        packs.require_text_binding("speech_to_text_v2")
    message = str(excinfo.value)
    assert "speech_to_text_v2" in message
    assert "not_implemented" in message
    assert packs.capability_executable("speech_to_text_v2") is False


def test_added_capability_needs_explicit_executable_truth(monkeypatch):
    # Even an "executable-looking" status cannot turn a new id on by itself: the
    # gate requires an explicit executable truth for that id.
    added = {"id": "speech_to_text_v2", "kind": "audio_to_text",
             "status": "local_mock_only", "usage_unit": "audio_duration"}
    monkeypatch.setattr(packs, "CAPABILITIES", packs.CAPABILITIES + (added,))
    assert packs.capability_executable("speech_to_text_v2") is False
    with pytest.raises(ValueError) as excinfo:
        packs.require_text_binding("speech_to_text_v2")
    assert "CAPABILITY_GATE" in str(excinfo.value)


def test_declared_status_can_revoke_an_executable_status(monkeypatch):
    # The declared table stays authoritative in the refusing direction too.
    revoked = tuple({**item, "status": "blocked"} if item["id"] == "mock_text" else item
                    for item in packs.CAPABILITIES)
    monkeypatch.setattr(packs, "CAPABILITIES", revoked)
    assert packs.capability_executable("mock_text") is False
    with pytest.raises(ValueError) as excinfo:
        packs.require_text_binding("mock_text")
    assert "blocked" in str(excinfo.value)


def test_question_pinned_with_mock_text_reports_context_unchanged(h):
    # Regression guard for _pin_context / question_context: the gate now reads a
    # table, so the accepted pin must still round-trip byte for byte.
    pid = h.register_project("fx")["project_id"]
    qid = h.capture_evaluation_input(pid, "What does the cost field mean?", None)
    pinned = h.question_context(qid)
    assert pinned["pack_id"] == packs.DEFAULT_PACK
    assert pinned["capability_id"] == "mock_text"
    assert pinned["manifest_sha256"] == packs.get_pack(packs.DEFAULT_PACK).identity()["manifest_sha256"]
    assert h.question_context(qid) == pinned
    # The non-executable path is unchanged in kind: still a Conflict, not a new error.
    for capability_id in ("meta_text", "transcription"):
        with pytest.raises(Conflict) as excinfo:
            h._pin_context("qid-probe-%s" % capability_id, packs.DEFAULT_PACK, capability_id)
        assert capability_id in str(excinfo.value)
