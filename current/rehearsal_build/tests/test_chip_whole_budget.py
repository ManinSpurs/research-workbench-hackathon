"""Whole-chip limits use expanded member bytes, not ZIP overhead."""
import io
import base64
import json
import zipfile

import pytest

from app import chips, chip_conversation
from test_chips import payload, zip_bytes


def parts(blob):
    with zipfile.ZipFile(io.BytesIO(blob)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_shipped_caps():
    for name in ("ART", "RECORD", "CONVERSATION", "TOTAL", "SOURCE"):
        assert getattr(chips, f"MAX_{name}_BYTES") == 100 * 1024 * 1024
    assert chips.MAX_MANIFEST_BYTES == 256 * 1024


def test_mixed_boundary_roundtrip(tmp_path, monkeypatch):
    store = chips.ChipStore(tmp_path)
    art = chips.PNG_MAGIC + b"x" * 32 + chips.PNG_SUFFIX
    store.install_bytes(chips.build_chip(payload(art_png=base64.b64encode(art).decode())))
    store.write_record("remote_sensing", {"harness": [{"key": "k", "value": "v"}]})
    before = store._target("remote_sensing").read_bytes()
    used = sum(map(len, parts(before).values()))
    monkeypatch.setattr(chips, "MAX_TOTAL_BYTES", used + 24)
    text = "한" * 8
    store.write_conversation("remote_sensing", text)
    original = store._target("remote_sensing").read_bytes()
    assert sum(map(len, parts(original).values())) == used + 24
    assert chips.read_chip_conversation(original) == text
    assert chips.read_chip_record(original).harness[0].value == "v"
    assert parts(original)["chip.png"] == art
    for action in (
        lambda: store.write_conversation("remote_sensing", text + "x"),
        lambda: store.write_record("remote_sensing", {"harness": [{"key": "k", "value": "vv"}]}),
    ):
        with pytest.raises(chips.Invalid, match="total limit"):
            action()
        assert store._target("remote_sensing").read_bytes() == original


def test_install_canonical_growth_preserves_original(tmp_path, monkeypatch):
    store = chips.ChipStore(tmp_path)
    store.install_bytes(chips.build_chip(payload()))
    target = store._target("remote_sensing")
    original = target.read_bytes()
    manifest = json.loads(parts(original)["manifest.json"])
    manifest["pack"]["version"] = "0.2.0"
    incoming = [
        ("manifest.json", json.dumps(manifest, separators=(",", ":")).encode()),
        ("record.json", b'{"questions":[{"text":"hello"}]}'),
        ("conversation.txt", b"verbatim\n"),
    ]
    monkeypatch.setattr(chips, "MAX_TOTAL_BYTES", sum(len(raw) for _, raw in incoming))
    raw = zip_bytes(incoming)
    chips.read_chip(raw)
    with pytest.raises(chips.Invalid, match="total limit"):
        store.install_bytes(raw)
    assert target.read_bytes() == original


def test_source_overhead_and_member_caps(tmp_path, monkeypatch):
    blob = chips.build_chip(payload())
    manifest, art = chips.read_chip(blob)
    monkeypatch.setattr(chips, "MAX_SOURCE_BYTES", len(blob) - 1)
    with pytest.raises(chips.Invalid, match="too large"):
        chips.read_chip(blob)
    with pytest.raises(chips.Invalid, match="too large"):
        chips._encode(manifest, art)


def test_fit_refuses_untruthful_tiny_notice():
    with pytest.raises(chips.Invalid, match="honest"):
        chip_conversation._fit(["x" * 1000], 10)
