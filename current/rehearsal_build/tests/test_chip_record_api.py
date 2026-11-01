"""The research record over HTTP: GET and PUT /chips/{pack_id}/record.

A chip's record is what makes it a notebook for one topic. These tests pin the two
routes that reach it: the body envelope is validated in the route, every rule and every
cap about the record itself comes from ``app/chips.py``, the record must really land
inside the ``.chip`` file on disk (not in a database or in memory), and the record is
data — nothing in it is ever executed, imported, compiled or evaluated.

Chips are made through the authoring path with the helper ``tests/test_chips.py`` uses,
so every id here is a chip a person could really build and install.
"""
import base64
import io
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import chips  # noqa: E402
from app.api import create_app  # noqa: E402
from test_chips import full_record, install, zip_bytes  # noqa: E402

PACK_ID = "remote_sensing"
EMPTY_RECORD = {"questions": [], "answers": [], "results": [], "code": [], "harness": []}


@pytest.fixture()
def client(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    with TestClient(create_app(str(tmp_path / "wb.db"), data)) as c:
        c.data_dir = data
        yield c


def installed(client, **overrides):
    """Installs one chip through the authoring path and returns its stored path."""
    response = install(client, **overrides)
    assert response.status_code == 200, response.json()
    return Path(response.json()["path"])


def chip_bytes(path):
    return path.read_bytes()


def members(path):
    """Every member of the chip on disk, read independently of the store."""
    with zipfile.ZipFile(io.BytesIO(chip_bytes(path))) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


# ------------------------------------------------------------------ reading
def test_a_freshly_installed_chip_reads_the_empty_record(client):
    installed(client)
    response = client.get(f"/chips/{PACK_ID}/record")
    assert response.status_code == 200
    assert response.json() == EMPTY_RECORD


def test_reading_an_unknown_chip_is_404_in_the_app_envelope(client):
    response = client.get("/chips/never_installed/record")
    assert response.status_code == 404
    body = response.json()
    assert set(body) == {"error", "message", "details"}
    assert body["error"] == "not_found"
    assert body["message"] == "never_installed"
    assert body["details"] == {}


# ------------------------------------------------------------------ round trip
def test_put_then_get_preserves_every_list(client):
    installed(client)
    record = full_record()
    written = client.put(f"/chips/{PACK_ID}/record", json={"record": record})
    assert written.status_code == 200, written.json()
    assert written.json() == record

    read_back = client.get(f"/chips/{PACK_ID}/record")
    assert read_back.status_code == 200
    assert read_back.json() == record
    # The named fields survive one by one, not just as an equal dump.
    assert read_back.json()["questions"][0]["text"] == "Does zoning cap supply?"
    assert read_back.json()["questions"][0]["manifest_sha256"] == "a" * 64
    assert read_back.json()["answers"][0]["kind"] == "note"
    assert json.loads(read_back.json()["results"][0]["body_json"]) == {"effect": -0.12, "n": 340}
    assert read_back.json()["code"][0]["source"] == "print('hello')\n"
    assert read_back.json()["harness"][0]["key"] == "unit_of_analysis"


def test_the_record_really_lands_inside_the_chip_file_on_disk(client):
    path = installed(client)
    before = chip_bytes(path)
    assert b"record.json" not in before, "a fresh chip carries no record yet"

    record = full_record()
    assert client.put(f"/chips/{PACK_ID}/record", json={"record": record}).status_code == 200

    # Independent of the store and the HTTP layer: open the file that is on disk.
    after = chip_bytes(path)
    with zipfile.ZipFile(io.BytesIO(after)) as archive:
        assert archive.namelist() == ["manifest.json", "record.json"]
        raw_record = archive.read("record.json")
    assert json.loads(raw_record.decode("utf-8")) == record
    # The module's own reader agrees with what the archive holds.
    assert chips.read_chip_record(after) == chips.Record.model_validate(record)
    # Canonical bytes, so equal records are equal bytes on disk.
    assert raw_record == chips.record_bytes(chips.Record.model_validate(record))


def test_manifest_and_art_are_unchanged_by_a_record_write(client):
    art = chips.render_chip_label({"title": "Remote Sensing"})
    path = installed(client, art_png=base64.b64encode(art).decode())
    before = members(path)
    assert set(before) == {"manifest.json", "chip.png"}
    assert client.get(f"/chips/{PACK_ID}/art").content == art

    assert client.put(f"/chips/{PACK_ID}/record", json={"record": full_record()}).status_code == 200

    after = members(path)
    assert after["manifest.json"] == before["manifest.json"], "the manifest bytes must not move"
    assert after["chip.png"] == before["chip.png"], "the art bytes must not move"
    assert after["chip.png"] == art
    # The identity the app reports is derived from the manifest, so it must not move either.
    assert client.get(f"/chips/{PACK_ID}/art").content == art
    assert client.get(f"/chips/{PACK_ID}").json()["identity"] == \
        chips.read_chip(chip_bytes(path))[0].pack.identity()


def test_put_replaces_the_whole_record(client):
    installed(client)
    assert client.put(f"/chips/{PACK_ID}/record", json={"record": full_record()}).status_code == 200
    replacement = {"harness": [{"key": "window", "value": "1961-2020", "added_at": "t"}]}
    assert client.put(f"/chips/{PACK_ID}/record", json={"record": replacement}).status_code == 200
    assert client.get(f"/chips/{PACK_ID}/record").json() == {
        "questions": [], "answers": [], "results": [], "code": [],
        "harness": [{"key": "window", "value": "1961-2020", "added_at": "t"}]}


def test_an_empty_record_put_removes_the_member_again(client):
    path = installed(client)
    assert client.put(f"/chips/{PACK_ID}/record", json={"record": full_record()}).status_code == 200
    assert "record.json" in members(path)
    assert client.put(f"/chips/{PACK_ID}/record", json={"record": {}}).status_code == 200
    assert set(members(path)) == {"manifest.json"}
    assert client.get(f"/chips/{PACK_ID}/record").json() == EMPTY_RECORD


def test_writing_an_unknown_chip_is_404(client):
    response = client.put("/chips/never_installed/record", json={"record": full_record()})
    assert response.status_code == 404
    assert response.json()["error"] == "not_found"
    assert response.json()["message"] == "never_installed"


# ------------------------------------------------------------------ built-in chips
def test_a_builtin_chip_reads_the_empty_record_not_a_404(client):
    # A built-in pack has no record of its own and its sample file is read-only in
    # spirit: it answers with the empty record shape, the same shape every chip uses.
    response = client.get("/chips/social_science/record")
    assert response.status_code == 200
    assert response.json() == EMPTY_RECORD


def test_writing_a_builtin_chip_is_refused_and_changes_nothing(client):
    before = (chips.BUILTIN_DIR / "social_science.chip").read_bytes()
    response = client.put("/chips/social_science/record", json={"record": full_record()})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_chip"
    assert body["message"] == "social_science is a built-in chip; built-in chips cannot be modified"
    assert body["details"] == {}
    # The sample file is byte-identical and the chip still reads as an empty record.
    assert (chips.BUILTIN_DIR / "social_science.chip").read_bytes() == before
    assert client.get("/chips/social_science/record").json() == EMPTY_RECORD
    assert client.get("/chips/social_science").status_code == 200


# ------------------------------------------------------------------ body envelope
@pytest.mark.parametrize("body, expected", [
    ({}, "record is required"),
    ({"record": None}, "record is required"),
    ({"record": "questions here"}, "must be a JSON object"),
    ({"record": []}, "must be a JSON object"),
    ({"record": 7}, "must be a JSON object"),
])
def test_missing_or_non_object_record_is_400(client, body, expected):
    installed(client)
    response = client.put(f"/chips/{PACK_ID}/record", json=body)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_chip"
    assert expected in response.json()["message"]
    assert response.json()["details"] == {}
    assert client.get(f"/chips/{PACK_ID}/record").json() == EMPTY_RECORD


def test_an_unknown_body_field_is_400_and_stores_nothing(client):
    installed(client)
    response = client.put(f"/chips/{PACK_ID}/record",
                          json={"record": {"harness": [{"key": "k", "value": "v"}]},
                                "shell": "rm -rf /"})
    assert response.status_code == 400
    body = response.json()
    assert body["error"] == "invalid_chip"
    assert body["message"] == "record does not accept the field(s): shell"
    assert body["details"] == {}
    assert client.get(f"/chips/{PACK_ID}/record").json() == EMPTY_RECORD


def test_an_unknown_record_field_is_400_through_the_module(client):
    # The record's own strictness is not restated in the route: chips.py refuses it.
    installed(client)
    response = client.put(f"/chips/{PACK_ID}/record",
                          json={"record": {"harness": [], "surprise": True}})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_chip"
    assert response.json()["message"].startswith("The record is not a valid chip record")
    assert client.get(f"/chips/{PACK_ID}/record").json() == EMPTY_RECORD


# ------------------------------------------------------------------ caps
def test_an_oversized_record_is_400_and_leaves_the_stored_one_intact(client, monkeypatch):
    # The shipped record cap is 100 MB, so the byte cap is pinned against a lowered cap.
    monkeypatch.setattr(chips, "MAX_RECORD_BYTES", 1024 * 1024)
    path = installed(client)
    original = full_record()
    assert client.put(f"/chips/{PACK_ID}/record", json={"record": original}).status_code == 200
    stored = chip_bytes(path)

    # Within the per-field text cap, but past the whole-record byte cap.
    fat = {"harness": [{"key": f"k{index}", "value": "v" * chips.MAX_RECORD_TEXT_CHARS}
                       for index in range(chips.MAX_RECORD_BYTES // chips.MAX_RECORD_TEXT_CHARS + 2)]}
    assert len(json.dumps(fat)) > chips.MAX_RECORD_BYTES
    response = client.put(f"/chips/{PACK_ID}/record", json={"record": fat})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_chip"
    assert "1 MB" in response.json()["message"]

    # Nothing was rewritten and the earlier record still reads back.
    assert chip_bytes(path) == stored
    assert client.get(f"/chips/{PACK_ID}/record").json() == original


def test_an_over_cap_entry_list_is_400_and_leaves_the_stored_one_intact(client):
    path = installed(client)
    original = full_record()
    assert client.put(f"/chips/{PACK_ID}/record", json={"record": original}).status_code == 200
    stored = chip_bytes(path)

    too_many = {"harness": [{"key": str(index)} for index in range(chips.MAX_RECORD_ENTRIES + 1)]}
    response = client.put(f"/chips/{PACK_ID}/record", json={"record": too_many})
    assert response.status_code == 400
    message = response.json()["message"]
    assert "harness" in message
    assert str(chips.MAX_RECORD_ENTRIES) in message
    assert "entries" in message

    assert chip_bytes(path) == stored
    assert client.get(f"/chips/{PACK_ID}/record").json() == original


def test_a_sub_cap_entry_list_is_accepted(client):
    # The cap is the module's number, not a smaller one invented by the route.
    installed(client)
    at_cap = {"harness": [{"key": str(index)} for index in range(chips.MAX_RECORD_ENTRIES)]}
    assert client.put(f"/chips/{PACK_ID}/record", json={"record": at_cap}).status_code == 200
    assert len(client.get(f"/chips/{PACK_ID}/record").json()["harness"]) == chips.MAX_RECORD_ENTRIES


# ------------------------------------------------------------------ data, never code
def test_hostile_code_is_stored_byte_for_byte_and_never_executed(client, tmp_path):
    path = installed(client)
    marker = tmp_path / "pwned-marker"
    assert not marker.exists()
    hostile = (
        "import os, subprocess\n"
        f'os.system("echo pwned > {marker}")\n'
        f"subprocess.run(['touch', '{marker}'])\n"
        f"eval(compile(\"open('{marker}', 'w')\", '<s>', 'exec'))\n"
        "os.popen('touch " + str(marker) + "'); $(whoami) `id` | && ; > < & {}\n"
        "print('$(touch x)'); def broken(:\n"  # deliberately not valid Python
    )
    shell = "#!/bin/sh\n$(touch " + str(marker) + ") `touch x` | cat\n"
    record = {"code": [{"name": "attack.py", "language": "python", "source": hostile,
                        "created_at": "2026-10-27T10:00:00Z"},
                       {"name": "attack.sh", "language": "shell", "source": shell,
                        "created_at": "2026-10-27T10:01:00Z"}],
              "harness": [{"key": "note", "value": "${IFS}$(id)"}]}

    # The stored record is the module's own validated form (every field filled in), so
    # the comparison is against what chips.py says this record is, not against my dict.
    expected = chips.Record.model_validate(record).model_dump(mode="json")
    written = client.put(f"/chips/{PACK_ID}/record", json={"record": record})
    assert written.status_code == 200
    assert written.json() == expected
    assert client.get(f"/chips/{PACK_ID}/record").json() == expected
    assert [row["source"] for row in written.json()["code"]] == [hostile, shell]

    # The bytes inside the archive are the same characters, unchanged.
    with zipfile.ZipFile(io.BytesIO(chip_bytes(path))) as archive:
        on_disk = json.loads(archive.read("record.json").decode("utf-8"))
    assert on_disk["code"][0]["source"] == hostile
    assert on_disk["code"][1]["source"] == shell

    # Nothing ran: the payload would have created this file.
    assert not marker.exists()


def test_a_record_is_never_echoed_as_html(client):
    installed(client)
    record = {"harness": [{"key": "<script>", "value": "<img src=x onerror=alert(1)>"}]}
    assert client.put(f"/chips/{PACK_ID}/record", json={"record": record}).status_code == 200
    response = client.get(f"/chips/{PACK_ID}/record")
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    # The text is data: it comes back as JSON, not as a document.
    assert response.json()["harness"][0]["value"] == "<img src=x onerror=alert(1)>"


# ------------------------------------------------------------------ a damaged stored record
def damage_record(path, data=b'{"harness": [{"key": "k", "surprise": 1}]}'):
    """Rewrites only record.json inside the chip on disk, leaving the manifest intact."""
    with zipfile.ZipFile(io.BytesIO(chip_bytes(path))) as archive:
        members = [(name, archive.read(name)) for name in archive.namelist()]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, payload in members:
            archive.writestr(name, data if name == "record.json" else payload)
    path.write_bytes(buffer.getvalue())
    return path.read_bytes()


def manifest_at_version(path, version):
    """This chip's own manifest.json at another version, so the pack id matches."""
    with zipfile.ZipFile(io.BytesIO(chip_bytes(path))) as archive:
        body = json.loads(archive.read("manifest.json").decode("utf-8"))
    body["pack"]["version"] = version
    return json.dumps(body).encode()


def test_a_damaged_stored_record_keeps_the_chip_visible_over_http(client):
    path = installed(client)
    assert client.put(f"/chips/{PACK_ID}/record", json={"record": full_record()}).status_code == 200
    damaged = damage_record(path)

    # The chip is still listed, and the listing names the damage instead of hiding it.
    listed = {row["pack_id"]: row for row in client.get("/chips").json()["chips"]}
    assert PACK_ID in listed
    assert "does not match the chip record" in listed[PACK_ID]["record_error"]

    # GET /chips/{id} still answers: the pack definition is intact.
    read = client.get(f"/chips/{PACK_ID}")
    assert read.status_code == 200
    assert read.json()["pack"]["pack_id"] == PACK_ID
    assert "does not match the chip record" in read.json()["record_error"]

    # Only the record route reports the damage, as a 400 that names the chip, never a
    # 404 that would claim the chip does not exist.
    record = client.get(f"/chips/{PACK_ID}/record")
    assert record.status_code == 400, record.json()
    assert record.json()["error"] == "invalid_chip"
    assert record.json()["message"].startswith(f"{PACK_ID} is stored with a record this build cannot read")

    # The bytes on disk are untouched by all of that reading.
    assert chip_bytes(path) == damaged


def test_an_install_cannot_overwrite_a_chip_whose_record_is_damaged(client):
    path = installed(client)
    assert client.put(f"/chips/{PACK_ID}/record", json={"record": full_record()}).status_code == 200
    damaged = damage_record(path)

    # Same version: the existing chip still counts as existing.
    same = install(client)
    assert same.status_code == 400
    assert "already installed" in same.json()["message"]
    assert chip_bytes(path) == damaged

    # A different version, carrying no record: carrying the damaged record forward is
    # impossible, so the install is refused instead of leaving ['manifest.json'].
    newer = install(client, version="0.2.0")
    assert newer.status_code == 400
    assert "cannot read" in newer.json()["message"]
    assert "export or delete" in newer.json()["message"]
    assert chip_bytes(path) == damaged
    assert set(members(path)) >= {"manifest.json", "record.json"}

    # A different version carrying its own record would also destroy the stored bytes.
    raw = zip_bytes([("manifest.json", manifest_at_version(path, "0.2.0")),
                     ("record.json", json.dumps({"harness": [{"key": "new"}]}).encode())])
    carries = client.post("/chips/install", json={"base64": base64.b64encode(raw).decode()})
    assert carries.status_code == 400
    assert "export or delete" in carries.json()["message"]
    assert chip_bytes(path) == damaged

    # The damaged chip is still listed after every refusal, and export hands its bytes out.
    assert PACK_ID in {row["pack_id"] for row in client.get("/chips").json()["chips"]}
    exported = client.post(f"/chips/export/{PACK_ID}")
    assert exported.status_code == 200
    assert base64.b64decode(exported.json()["base64"]) == damaged
