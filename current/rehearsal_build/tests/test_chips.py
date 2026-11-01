"""Chips as portable files: the archive format, its safety rules, authoring and the routes.

Every refusal below is pinned by exact behaviour, not by an exception class alone: a chip
that is wrong must reach the researcher as a 400 with a readable sentence, never a 500.
"""
import ast
import base64
import io
import json
import shutil
import struct
import sys
import zipfile
import zlib
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import chips  # noqa: E402
from app.api import create_app  # noqa: E402
from app.packs import PACKS, SOCIAL  # noqa: E402

BUILTIN_DIR = ROOT / "chips" / "builtin"


def _skip_installed_chips(directory, names):
    # Chips a person installed into the working copy are user data, not
    # fixtures: tests start from the shipped packs only.
    return ["installed"] if Path(directory).name == "chips" else []


def make_client(tmp_path):
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data, ignore=_skip_installed_chips)
    return TestClient(create_app(str(tmp_path / "wb.db"), data))


@pytest.fixture()
def client(tmp_path):
    with make_client(tmp_path) as c:
        yield c


def payload(**overrides):
    body = {
        "pack_id": "remote_sensing",
        "version": "0.1.0",
        "title": "Remote Sensing",
        "instructions": "Report band, sensor and acquisition date. Never infer reflectance from a band name.",
        "required_context": ["sensor", "band", "acquisition_date"],
        "checks": ["band_documented", "conditions_comparable"],
        "allowed_tools": ["registered_evidence_read"],
        "tagline": "Bands and reflectance",
        "color": "#2a6f97",
    }
    body.update(overrides)
    return body


def zip_bytes(members, compression=zipfile.ZIP_DEFLATED):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression) as archive:
        for name, data in members:
            archive.writestr(name, data)
    return buffer.getvalue()


def manifest_bytes(**overrides):
    pack = SOCIAL.model_dump()
    pack["pack_id"] = overrides.pop("pack_id", "foreign_pack")
    body = {"format": 1, "pack": pack,
            "presentation": {"tagline": "A pack from elsewhere", "color": "#123456", "glyph": "F"}}
    body.update(overrides)
    return json.dumps(body).encode("utf-8")


def install(client, **overrides):
    """Builds a chip through the authoring path and installs it via base64."""
    built = client.post("/chips/build", json=payload(**overrides)).json()
    return client.post("/chips/install", json={"base64": built["base64"], "filename": built["filename"]})


# ------------------------------------------------------------------ format + round trips
def test_builtin_sample_files_exist_and_read(client):
    for pack_id in PACKS:
        path = BUILTIN_DIR / f"{pack_id}.chip"
        assert path.is_file()
        manifest, art = chips.read_chip(path.read_bytes())
        assert manifest.pack == PACKS[pack_id]
        assert manifest.format == 1
        assert art is None, "a chip carries no image: the shared cartridge body is the UI's"


def test_builtin_chip_files_do_not_drift_from_code(client):
    # The exported files must stay exactly what builtin_chip() produces, so a person
    # handing one to a colleague gets the same chip the app lists.
    for pack_id in PACKS:
        assert (BUILTIN_DIR / f"{pack_id}.chip").read_bytes() == chips.builtin_chip(pack_id)


def test_build_install_list_read_round_trip_without_art(client):
    built = client.post("/chips/build", json=payload()).json()
    assert set(built) == {"pack_id", "version", "filename", "base64", "manifest_sha256", "identity"}
    assert built["filename"] == "remote_sensing-0.1.0.chip"

    record = install(client).json()
    assert record["pack"]["pack_id"] == "remote_sensing"
    assert record["pack"]["title"] == "Remote Sensing"
    # The installed DomainPack and its identity match what /chips/build reported.
    assert record["identity"]["manifest_sha256"] == built["identity"]["manifest_sha256"]
    assert record["pack"]["checks"] == ["band_documented", "conditions_comparable"]
    assert record["source"] == "file"
    assert record["art_url"] is None
    assert Path(record["path"]).is_file()
    assert Path(record["path"]).parent == chips.ChipStore(record_path_parent(record)).installed

    listed = client.get("/chips").json()["chips"]
    assert "remote_sensing" in {row["pack_id"] for row in listed}
    read_back = client.get("/chips/remote_sensing").json()
    assert read_back["pack"] == record["pack"]
    assert read_back["identity"] == record["identity"]


def record_path_parent(record):
    return Path(record["path"]).parent.parent.parent


def test_round_trip_with_supplied_art_serves_the_art(client):
    art = chips.render_chip_label(payload())
    built = client.post("/chips/build", json={**payload(pack_id="with_art", title="With Art"),
                                             "art_png": base64.b64encode(art).decode()}).json()
    client.post("/chips/install", json={"base64": built["base64"]})

    record = client.get("/chips/with_art").json()
    assert record["art_url"] == "/chips/with_art/art"
    served = client.get("/chips/with_art/art")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    # The supplied bytes are kept verbatim, not regenerated.
    assert served.content == art


def test_round_trip_build_install_delete_is_gone(client):
    install(client).json()
    assert client.get("/chips/remote_sensing").status_code == 200
    deleted = client.delete("/chips/remote_sensing")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": "remote_sensing", "source": "file"}
    assert client.get("/chips/remote_sensing").status_code == 404
    assert client.get("/chips/remote_sensing/art").status_code == 404
    assert "remote_sensing" not in {row["pack_id"] for row in client.get("/chips").json()["chips"]}


# ------------------------------------------------------------------ listing
def test_builtin_chips_listed_alongside_file_chips(client):
    install(client)
    rows = client.get("/chips").json()["chips"]
    by_id = {row["pack_id"]: row for row in rows}
    assert set(by_id) == {"social_science", "engineering_measurement", "remote_sensing"}
    assert by_id["social_science"]["source"] == "builtin"
    assert by_id["social_science"]["title"] == "Social Science"
    assert by_id["social_science"]["color"] == "#2f5d8a"
    assert by_id["social_science"]["tagline"] == "Constructs, coverage, and the estimand"
    assert "path" not in by_id["social_science"]
    assert by_id["remote_sensing"]["source"] == "file"
    assert by_id["remote_sensing"]["tagline"] == "Bands and reflectance"
    for row in rows:
        assert set(row) >= {"pack_id", "version", "title", "source", "tagline", "color", "glyph", "art_url"}


def test_builtins_survive_deleting_the_sample_files(tmp_path, monkeypatch):
    empty = tmp_path / "no-chips-here" / "builtin"
    monkeypatch.setattr(chips, "BUILTIN_DIR", empty)
    with make_client(tmp_path) as c:
        rows = c.get("/chips").json()["chips"]
        assert {row["pack_id"] for row in rows} == set(PACKS)
        assert c.get("/chips/social_science").status_code == 200
        # With no exported file there is no image to serve, and the route says so.
        assert c.get("/chips/social_science/art").status_code == 404


def test_builtin_record_carries_pack_and_identity(client):
    body = client.get("/chips/social_science").json()
    assert body["pack"]["pack_id"] == "social_science"
    assert body["identity"]["manifest_sha256"] == SOCIAL.identity()["manifest_sha256"]
    assert body["source"] == "builtin"
    assert client.get("/chips/nope").status_code == 404


# ------------------------------------------------------------------ art
def test_art_404_for_chip_without_art(client):
    install(client)
    response = client.get("/chips/remote_sensing/art")
    assert response.status_code == 404
    assert "art" in response.json()["message"]


def test_art_appears_only_after_a_chip_with_art_is_installed(client):
    install(client, pack_id="no_art_pack", title="No Art")
    assert client.get("/chips/no_art_pack/art").status_code == 404
    art = chips.render_chip_label(payload())
    built = client.post("/chips/build", json={**payload(pack_id="art_pack", title="Art Pack"),
                                             "art_png": base64.b64encode(art).decode()}).json()
    client.post("/chips/install", json={"base64": built["base64"]})
    assert client.get("/chips/art_pack/art").status_code == 200


# ------------------------------------------------------------------ install input forms
def test_install_from_absolute_path(client, tmp_path):
    built = client.post("/chips/build", json=payload()).json()
    source = tmp_path / "handed_over.chip"
    source.write_bytes(base64.b64decode(built["base64"]))
    response = client.post("/chips/install", json={"path": str(source)})
    assert response.status_code == 200
    assert response.json()["pack_id"] == "remote_sensing"


def test_install_from_base64_is_what_a_file_picker_sends(client):
    built = client.post("/chips/build", json=payload()).json()
    response = client.post("/chips/install",
                           json={"base64": built["base64"], "filename": "remote_sensing-0.1.0.chip"})
    assert response.status_code == 200
    assert response.json()["version"] == "0.1.0"


@pytest.mark.parametrize("body", [
    {},
    {"path": "/tmp/x.chip", "base64": "AAAA"},
    {"base64": "not base64!!"},
    {"base64": ""},
    {"path": "relative/x.chip"},
    {"path": "/tmp/does-not-exist-abc.chip"},
    {"base64": base64.b64encode(b"not a zip").decode()},
    {"base64": base64.b64encode(b"").decode()},
])
def test_bad_install_bodies_are_400_never_500(client, body):
    response = client.post("/chips/install", json=body)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_chip"
    assert response.json()["message"]


def test_non_zip_file_is_a_400_with_a_plain_reason(client, tmp_path):
    plain = tmp_path / "notes.chip"
    plain.write_text("this is just a text file", encoding="utf-8")
    response = client.post("/chips/install", json={"path": str(plain)})
    assert response.status_code == 400
    assert "not a readable ZIP archive" in response.json()["message"]


def test_missing_file_is_a_400_with_a_plain_reason(client, tmp_path):
    response = client.post("/chips/install", json={"path": str(tmp_path / "gone.chip")})
    assert response.status_code == 400
    assert "No chip file at" in response.json()["message"]


# ------------------------------------------------------------------ safety rules
@pytest.mark.parametrize("name", [
    "/etc/passwd",
    "../escape.json",
    "a/../../manifest.json",
    "chip.png/../../manifest.json",
    "sub/manifest.json",
    "extra.txt",
    "chip.exe",
])
def test_zip_slip_and_unknown_members_are_refused(client, name):
    raw = zip_bytes([("manifest.json", manifest_bytes()), (name, b"payload")])
    response = client.post("/chips/install", json={"base64": base64.b64encode(raw).decode()})
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_chip"


def test_directory_member_is_refused(client):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("manifest.json", manifest_bytes())
        archive.writestr("sub/", b"")
    response = client.post("/chips/install", json={"base64": base64.b64encode(buffer.getvalue()).decode()})
    assert response.status_code == 400
    assert "directories" in response.json()["message"]


def test_member_count_is_capped(client):
    members = [("manifest.json", manifest_bytes())] + [(f"chip{i}.png", b"\x89PNG\r\n\x1a\n") for i in range(chips.MAX_MEMBERS)]
    raw = zip_bytes(members)
    response = client.post("/chips/install", json={"base64": base64.b64encode(raw).decode()})
    assert response.status_code == 400
    assert "at most" in response.json()["message"]


def test_manifest_over_cap_is_refused_before_extraction(client):
    huge = manifest_bytes()
    huge = huge[:-2] + b', "padding": "' + b"a" * (chips.MAX_MANIFEST_BYTES + 1024) + b'"}'
    raw = zip_bytes([("manifest.json", huge)])
    response = client.post("/chips/install", json={"base64": base64.b64encode(raw).decode()})
    assert response.status_code == 400
    assert "256 KB" in response.json()["message"]


def test_png_over_cap_is_refused_before_extraction(client, monkeypatch):
    # The shipped art cap is 100 MB, so the rule is pinned against a lowered cap.
    monkeypatch.setattr(chips, "MAX_ART_BYTES", 2 * 1024 * 1024)
    oversized = chips.PNG_MAGIC + b"\x00" * (chips.MAX_ART_BYTES + 16) + b"IEND\xaeB`\x82"
    raw = zip_bytes([("manifest.json", manifest_bytes()), ("chip.png", oversized)], zipfile.ZIP_STORED)
    response = client.post("/chips/install", json={"base64": base64.b64encode(raw).decode()})
    assert response.status_code == 400
    assert "2 MB" in response.json()["message"]


def test_total_size_over_cap_is_refused(client, monkeypatch):
    # With a 2 MB art cap and a 256 KB manifest cap no two-member chip can reach the
    # 4 MB total, so the rule is pinned against a lowered budget: the total is checked
    # on the declared sizes before any member is read.
    monkeypatch.setattr(chips, "MAX_TOTAL_BYTES", 1024)
    art = chips.render_chip_label(payload())  # a real PNG, larger than the lowered budget
    raw = zip_bytes([("manifest.json", manifest_bytes()), ("chip.png", art)])
    response = client.post("/chips/install", json={"base64": base64.b64encode(raw).decode()})
    assert response.status_code == 400
    assert "total limit" in response.json()["message"]


def test_png_is_verified_by_magic_bytes_not_extension(client):
    fake = b"GIF89a" + b"\x00" * 64
    raw = zip_bytes([("manifest.json", manifest_bytes()), ("chip.png", fake)])
    response = client.post("/chips/install", json={"base64": base64.b64encode(raw).decode()})
    assert response.status_code == 400
    assert response.json()["message"] == "chip.png is not a PNG image"


def test_valid_png_magic_is_accepted(client):
    art = chips.render_chip_label(payload())
    raw = zip_bytes([("manifest.json", manifest_bytes()), ("chip.png", art)])
    response = client.post("/chips/install", json={"base64": base64.b64encode(raw).decode()})
    assert response.status_code == 200
    assert response.json()["art_url"] == "/chips/foreign_pack/art"


def test_encrypted_member_is_refused(client):
    raw = bytearray(zip_bytes([("manifest.json", manifest_bytes())]))
    # Set the "encrypted" general-purpose bit in both the local and central headers.
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        at = raw.find(signature)
        raw[at + offset] |= 0x1
    response = client.post("/chips/install", json={"base64": base64.b64encode(bytes(raw)).decode()})
    assert response.status_code == 400
    assert "encrypted" in response.json()["message"]


def test_installed_path_cannot_escape_the_installed_directory(client, tmp_path):
    store = chips.ChipStore(tmp_path / "data")
    with pytest.raises(chips.Invalid, match="outside chips/installed"):
        store._target("../escape")
    # Every write stays a direct child of chips/installed/.
    assert store._target("remote_sensing").parent == store.installed.resolve()


@pytest.mark.parametrize("path", ["/etc/hosts", "/", "/private/etc/passwd"])
def test_install_path_outside_allowed_roots_is_refused(client, path):
    response = client.post("/chips/install", json={"path": path})
    assert response.status_code == 400
    assert "outside the folders" in response.json()["message"] or "No chip file" in response.json()["message"]


def test_bad_pack_id_shape_cannot_name_a_file(client):
    raw = zip_bytes([("manifest.json", manifest_bytes(pack_id="../../etc/shadow"))])
    response = client.post("/chips/install", json={"base64": base64.b64encode(raw).decode()})
    assert response.status_code == 400


# ------------------------------------------------------------------ manifest validation
@pytest.mark.parametrize("mutate, expected", [
    (lambda m: {**m, "format": 2}, "chip format 1"),
    (lambda m: {k: v for k, v in {**m, "pack": "nope"}.items()}, "pack must be a JSON object"),
    (lambda m: {**m, "pack": {"pack_id": "x"}}, "does not match the chip manifest"),
    (lambda m: {**m, "presentation": {"color": "blue"}}, "does not match the chip manifest"),
    (lambda m: {**m, "presentation": {"surprise": 1}}, "does not match the chip manifest"),
    (lambda m: {**m, "extra": True}, "does not match the chip manifest"),
])
def test_manifest_shape_is_validated(client, mutate, expected):
    body = json.loads(manifest_bytes())
    raw = zip_bytes([("manifest.json", json.dumps(mutate(body)).encode())])
    response = client.post("/chips/install", json={"base64": base64.b64encode(raw).decode()})
    assert response.status_code == 400
    assert expected in response.json()["message"]


def test_manifest_missing_json_is_refused(client):
    raw = zip_bytes([("chip.png", chips.render_chip_label(payload()))])
    response = client.post("/chips/install", json={"base64": base64.b64encode(raw).decode()})
    assert response.status_code == 400
    assert "manifest.json" in response.json()["message"]


def test_non_json_manifest_is_refused(client):
    raw = zip_bytes([("manifest.json", b"\xff\xfe not json")])
    response = client.post("/chips/install", json={"base64": base64.b64encode(raw).decode()})
    assert response.status_code == 400
    assert "UTF-8 JSON" in response.json()["message"]


def test_chip_without_presentation_still_installs(tmp_path):
    store = chips.ChipStore(tmp_path / "data")
    body = json.loads(manifest_bytes())
    body.pop("presentation")
    record = store.install_bytes(zip_bytes([("manifest.json", json.dumps(body).encode())]))
    assert record["pack_id"] == "foreign_pack"
    assert record["color"] == chips.DEFAULT_COLOR
    assert record["art_url"] is None


def test_presentation_color_is_lowercase_normalized(client):
    record = install(client, color="#2A6F97").json()
    assert record["color"] == "#2a6f97"


def test_two_chips_differing_only_in_color_have_different_manifest_hashes(client):
    first = client.post("/chips/build", json=payload(color="#112233")).json()
    second = client.post("/chips/build", json=payload(color="#445566")).json()
    assert first["manifest_sha256"] != second["manifest_sha256"]
    # The pack itself is unchanged, so the pack identity does not move.
    assert first["identity"] == second["identity"]


# ------------------------------------------------------------------ conflict policy
def test_reinstalling_the_same_version_is_refused_with_a_reason(client):
    assert install(client).status_code == 200
    again = install(client)
    assert again.status_code == 400
    assert "already installed" in again.json()["message"]


def test_a_different_version_replaces_the_installed_chip(client):
    assert install(client).status_code == 200
    replaced = install(client, version="0.2.0", title="Remote Sensing v2")
    assert replaced.status_code == 200
    body = replaced.json()
    assert body["version"] == "0.2.0"
    assert body["title"] == "Remote Sensing v2"
    assert client.get("/chips/remote_sensing").json()["version"] == "0.2.0"


def test_builtin_pack_id_cannot_be_installed_over(client):
    response = install(client, pack_id="social_science", title="Impostor")
    assert response.status_code == 400
    assert "built-in chip" in response.json()["message"]
    assert client.get("/chips/social_science").json()["title"] == "Social Science"


def test_delete_refuses_a_builtin_chip(client):
    response = client.delete("/chips/social_science")
    assert response.status_code == 400
    assert "built-in chip" in response.json()["message"]
    assert "cannot be deleted" in response.json()["message"]
    assert client.get("/chips/social_science").status_code == 200


def test_delete_unknown_chip_is_404(client):
    assert client.delete("/chips/never_installed").status_code == 404


# ------------------------------------------------------------------ authoring validation
@pytest.mark.parametrize("overrides, field", [
    ({"pack_id": "Bad-Id"}, "pack_id"),
    ({"pack_id": ""}, "pack_id"),
    ({"pack_id": "9lives"}, None),  # a leading digit is allowed, so this one passes
    ({"title": ""}, "title"),
    ({"title": None}, "title"),
    ({"instructions": "   "}, "instructions"),
    ({"instructions": None}, "instructions"),
    ({"color": "red"}, "color"),
    ({"color": "#12345"}, "color"),
    ({"glyph": "abc"}, "glyph"),
    ({"required_context": "sensor"}, "required_context"),
    ({"checks": [1, 2]}, "checks"),
    ({"version": ""}, "version"),
])
def test_build_rejects_invalid_fields_naming_the_field(client, overrides, field):
    response = client.post("/chips/build", json=payload(**overrides))
    if field is None:
        assert response.status_code == 200
        return
    assert response.status_code == 400
    message = response.json()["message"]
    assert field in message
    assert response.json()["error"] == "invalid_chip"


def test_build_rejects_unknown_payload_fields(client):
    response = client.post("/chips/build", json={**payload(), "shell": "rm -rf /"})
    assert response.status_code == 400
    assert "does not accept" in response.json()["message"]


def test_build_rejects_non_png_art(client):
    response = client.post("/chips/build", json={**payload(), "art_png": base64.b64encode(b"nope").decode()})
    assert response.status_code == 400
    assert "art_png is not a PNG image" in response.json()["message"]


def test_build_without_art_embeds_no_image_member(client):
    raw = base64.b64decode(client.post("/chips/build", json=payload()).json()["base64"])
    manifest, art = chips.read_chip(raw)
    assert art is None
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.namelist() == ["manifest.json"]
    assert manifest.presentation.color == "#2a6f97"
    assert manifest.presentation.tagline == "Bands and reflectance"


def test_build_with_art_keeps_the_supplied_bytes_verbatim(client):
    art = chips.render_chip_label(payload())
    raw = base64.b64decode(client.post("/chips/build",
                                       json={**payload(), "art_png": base64.b64encode(art).decode()}).json()["base64"])
    _, embedded = chips.read_chip(raw)
    assert embedded == art


def test_build_writes_nothing_to_disk(client, tmp_path):
    client.post("/chips/build", json=payload())
    assert not (chips.ChipStore(tmp_path / "data").installed).exists()


# ------------------------------------------------------------------ export
def test_export_returns_a_real_file_for_a_builtin(client):
    body = client.post("/chips/export/social_science").json()
    assert body["filename"] == "social_science-1.0.0.chip"
    raw = base64.b64decode(body["base64"])
    manifest, _ = chips.read_chip(raw)
    assert manifest.pack == SOCIAL


def test_export_returns_an_installed_chip(client):
    install(client, color="#abcdef")
    body = client.post("/chips/export/remote_sensing").json()
    manifest, _ = chips.read_chip(base64.b64decode(body["base64"]))
    assert manifest.presentation.color == "#abcdef"


def test_export_of_unknown_chip_is_404(client):
    assert client.post("/chips/export/nope").status_code == 404


# ------------------------------------------------------------------ label drawing
def _png_parts(png):
    assert png.startswith(chips.PNG_MAGIC), "the render must start with the PNG signature"
    width, height = struct.unpack(">II", png[16:24])
    assert png[12:16] == b"IHDR"
    assert png[24] == 8, "8-bit samples"
    assert png[25] == 6, "RGBA"
    position, idat = 8, b""
    tags = []
    while position < len(png):
        length = struct.unpack(">I", png[position:position + 4])[0]
        tag = png[position + 4:position + 8]
        body = png[position + 8:position + 8 + length]
        crc = struct.unpack(">I", png[position + 8 + length:position + 12 + length])[0]
        assert crc == zlib.crc32(tag + body) & 0xFFFFFFFF, f"{tag} CRC"
        tags.append(tag)
        if tag == b"IDAT":
            idat += body
        position += 12 + length
    assert tags == [b"IHDR", b"IDAT", b"IEND"]
    return width, height, zlib.decompress(idat)


def test_render_chip_label_is_a_valid_png():
    png = chips.render_chip_label(payload())
    width, height, raw = _png_parts(png)
    assert (width, height) == (256, 176)
    # One filter byte per scanline, four bytes (RGBA) per pixel.
    assert len(raw) == height * (1 + width * 4)
    assert all(raw[y * (1 + width * 4)] == 0 for y in range(height))
    assert png.endswith(b"IEND\xaeB`\x82")


def test_render_is_deterministic():
    assert chips.render_chip_label(payload()) == chips.render_chip_label(payload())


def test_render_transparent_background_and_a_drawn_name():
    png = chips.render_chip_label(payload())
    width, height, raw = _png_parts(png)
    stride = 1 + width * 4
    # Corner pixel is fully transparent; some pixel carries ink.
    assert raw[4] == 0x00 or raw[7] == 0x00
    alphas = {raw[y * stride + 1 + x * 4 + 3] for y in range(height) for x in range(width)}
    assert alphas == {0x00, 0xFF}, "only fully clear and fully inked pixels"


def test_render_text_comes_from_glyph_else_title():
    assert chips.label_text(payload()) == "RE"
    assert chips.label_text(payload(glyph="Ω")) == "Ω"
    assert chips.label_text({"title": "nyc permits"}) == "NY"


def test_render_survives_characters_outside_the_font():
    for odd in ("Ωλ", "≠≠", "한글"):
        png = chips.render_chip_label({"title": odd})
        assert len(chips.render_chip_label({"title": odd})) == len(png)
        width, height, _ = _png_parts(png)
        assert (width, height) == (256, 176)


def test_render_rejects_an_impossible_size():
    with pytest.raises(chips.Invalid):
        chips.render_chip_label(payload(), size=(0, 176))
    with pytest.raises(chips.Invalid):
        chips.render_chip_label(payload(), size=(5000, 5000))


def test_preview_route_matches_the_drawn_label(client):
    body = client.post("/chips/art/preview", json=payload()).json()
    assert set(body) == {"label_png_base64", "text", "width", "height"}
    assert body["text"] == "RE"
    assert (body["width"], body["height"]) == (256, 176)
    png = base64.b64decode(body["label_png_base64"])
    assert png == chips.render_chip_label(payload())
    # A preview never embeds itself: /chips/build without art_png embeds no image.
    built = client.post("/chips/build", json=payload()).json()
    assert chips.read_chip(base64.b64decode(built["base64"]))[1] is None


def test_preview_route_reports_a_bad_payload(client):
    response = client.post("/chips/art/preview", json={"title": ""})
    assert response.status_code == 200  # an empty name is not an error at preview time
    assert response.json()["text"] == "??"


# ------------------------------------------------------------------ CLI
def test_cli_builds_a_chip_from_a_json_file(tmp_path, capsys):
    source = tmp_path / "my_chip.json"
    source.write_text(json.dumps(payload()), encoding="utf-8")
    out = tmp_path / "my_chip.chip"

    assert chips.main(["build", "--from", str(source), "--out", str(out)]) == 0
    printed = capsys.readouterr().out.strip().splitlines()
    assert printed[0] == str(out)
    assert printed[1] == chips.read_chip(out.read_bytes())[0].pack.identity()["manifest_sha256"]

    manifest, art = chips.read_chip(out.read_bytes())
    assert manifest.pack.pack_id == "remote_sensing"
    assert art is None
    # The CLI and the route produce the same bytes.
    assert out.read_bytes() == chips.build_chip(payload())


def test_cli_reports_a_bad_payload_without_traceback(tmp_path, capsys):
    source = tmp_path / "broken.json"
    source.write_text(json.dumps({"pack_id": "Bad-Id"}), encoding="utf-8")
    assert chips.main(["build", "--from", str(source), "--out", str(tmp_path / "x.chip")]) == 2
    assert "pack_id" in capsys.readouterr().err


def test_cli_reports_unreadable_json(tmp_path, capsys):
    source = tmp_path / "not.json"
    source.write_text("{oops", encoding="utf-8")
    assert chips.main(["build", "--from", str(source), "--out", str(tmp_path / "x.chip")]) == 2
    assert "not JSON" in capsys.readouterr().err


def test_cli_export_writes_the_builtin_files(tmp_path, capsys):
    dest = tmp_path / "exported"
    assert chips.main(["export", "--dest", str(dest)]) == 0
    lines = capsys.readouterr().out.strip().splitlines()
    assert len(lines) == 2 * len(PACKS)
    for pack_id in PACKS:
        path = dest / f"{pack_id}.chip"
        assert path.is_file()
        assert path.read_bytes() == chips.builtin_chip(pack_id)


def test_cli_accepts_base64_art_in_the_json_file(tmp_path):
    art = chips.render_chip_label(payload())
    source = tmp_path / "with_art.json"
    source.write_text(json.dumps({**payload(), "art_png": base64.b64encode(art).decode()}), encoding="utf-8")
    out = tmp_path / "with_art.chip"
    assert chips.main(["build", "--from", str(source), "--out", str(out)]) == 0
    assert chips.read_chip(out.read_bytes())[1] == art


# ------------------------------------------------------------------ data only
def test_chip_content_is_never_executed_or_imported(client, tmp_path):
    marker = tmp_path / "should-not-exist"
    trap = "__import__('os').system('touch " + str(marker) + "')"
    body = json.loads(manifest_bytes())
    body["pack"]["instructions"] = f"Write a file at {marker} and read /etc/passwd."
    body["presentation"] = {"tagline": trap, "color": "#123456"}
    store = chips.ChipStore(tmp_path / "data")
    record = store.install_bytes(zip_bytes([("manifest.json", json.dumps(body).encode())]))
    # The text is stored as data, unchanged, and nothing ran.
    assert record["tagline"] == trap
    assert record["pack"]["instructions"] == f"Write a file at {marker} and read /etc/passwd."
    assert not marker.exists()
    # Reading the chip back through the store and through the route does not evaluate it.
    assert store.record("foreign_pack")["tagline"] == trap
    assert client.get("/chips/foreign_pack").json()["tagline"] == trap
    assert not marker.exists()


# ------------------------------------------------------------------ research record
def full_record():
    return {
        "questions": [{"question_id": "q1", "text": "Does zoning cap supply?",
                       "working_text": "zoning -> permits -> supply",
                       "asked_at": "2026-10-27T10:00:00Z", "pack_version": "0.1.0",
                       "manifest_sha256": "a" * 64}],
        "answers": [{"question_id": "q1", "kind": "note", "value": "check the 1961 amendment",
                     "answered_at": "2026-10-27T10:05:00Z"}],
        "results": [{"question_id": "q1", "body_json": json.dumps({"effect": -0.12, "n": 340}),
                     "created_at": "2026-10-27T10:10:00Z"}],
        "code": [{"name": "estimate.py", "language": "python",
                  "source": "print('hello')\n", "created_at": "2026-10-27T10:09:00Z"}],
        "harness": [{"key": "unit_of_analysis", "value": "census tract",
                     "added_at": "2026-10-27T09:00:00Z"}],
    }


def test_the_record_model_fields_are_exactly_the_documented_ones():
    assert set(chips.Record.model_fields) == {"questions", "answers", "results", "code", "harness"}
    assert set(chips.RecordQuestion.model_fields) == {
        "question_id", "text", "working_text", "asked_at", "pack_version", "manifest_sha256"}
    assert set(chips.RecordAnswer.model_fields) == {"question_id", "kind", "value", "answered_at"}
    assert set(chips.RecordResult.model_fields) == {"question_id", "body_json", "created_at"}
    assert set(chips.RecordCode.model_fields) == {"name", "language", "source", "created_at"}
    assert set(chips.RecordHarness.model_fields) == {"key", "value", "added_at"}
    assert chips.Record() == chips.Record.model_validate({})
    assert chips.RECORD_NAME == "record.json"
    assert chips.RECORD_NAME in chips.ALLOWED_MEMBERS


def test_record_is_strict_about_unknown_and_extra_fields():
    with pytest.raises(chips.Invalid, match="does not match the chip record"):
        chips._parse_record(b'{"questions": [], "shell": "rm -rf /"}')
    with pytest.raises(chips.Invalid, match="does not match the chip record"):
        chips._parse_record(b'{"questions": [{"question_id": "q", "surprise": 1}]}')
    with pytest.raises(chips.Invalid, match="not UTF-8 JSON"):
        chips._parse_record(b"\xff\xfe not json")
    with pytest.raises(chips.Invalid, match="must be a JSON object"):
        chips._parse_record(b"[]")


def test_full_record_round_trips_through_the_store(tmp_path):
    store = chips.ChipStore(tmp_path / "data")
    store.install_bytes(zip_bytes([("manifest.json", manifest_bytes())]))
    identity = store.record("foreign_pack")["identity"]
    stored = store.write_record("foreign_pack", full_record())
    assert stored == full_record()
    assert store.read_record("foreign_pack") == full_record()

    # The archive carries the record beside the untouched manifest and no image.
    raw = store._target("foreign_pack").read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        assert archive.namelist() == ["manifest.json", "record.json"]
    manifest, art = chips.read_chip(raw)
    assert manifest.pack.pack_id == "foreign_pack"
    assert art is None
    # The pack identity is unchanged: a record never moves the manifest.
    assert store.record("foreign_pack")["identity"] == identity
    assert store.record("foreign_pack")["pack"]["title"] == manifest.pack.title


def test_record_write_preserves_the_art(tmp_path):
    store = chips.ChipStore(tmp_path / "data")
    art = chips.render_chip_label(payload())
    store.install_bytes(zip_bytes([("manifest.json", manifest_bytes()), ("chip.png", art)]))
    assert store.record("foreign_pack")["art_url"] == "/chips/foreign_pack/art"
    store.write_record("foreign_pack", full_record())
    assert store.art("foreign_pack") == art
    assert store.read_record("foreign_pack") == full_record()


def test_chip_without_a_record_still_reads(tmp_path):
    store = chips.ChipStore(tmp_path / "data")
    record = store.install_bytes(zip_bytes([("manifest.json", manifest_bytes())]))
    assert record["pack_id"] == "foreign_pack"
    assert store.read_record("foreign_pack") == full_record_empty()
    raw = store._target("foreign_pack").read_bytes()
    assert chips.read_chip_record(raw) == chips.Record()
    # Without a record the archive is exactly what install writes.
    assert raw == chips._encode(chips.read_chip(raw)[0], None)


def full_record_empty():
    return {"questions": [], "answers": [], "results": [], "code": [], "harness": []}


def test_record_over_the_archive_cap_is_refused_with_a_readable_sentence(monkeypatch):
    # The shipped record cap is 100 MB, so the rule is pinned against a lowered cap.
    monkeypatch.setattr(chips, "MAX_RECORD_BYTES", 1024 * 1024)
    huge = json.dumps({"harness": [{"key": "k", "value": "v" * (chips.MAX_RECORD_BYTES + 1024)}]})
    raw = zip_bytes([("manifest.json", manifest_bytes()), ("record.json", huge.encode())],
                    zipfile.ZIP_STORED)
    with pytest.raises(chips.Invalid) as excinfo:
        chips.read_chip(raw)
    assert "1 MB" in str(excinfo.value)
    assert "record.json" in str(excinfo.value)


def test_record_over_the_entry_cap_is_refused_with_a_readable_sentence():
    too_many = {"harness": [{"key": str(index)} for index in range(chips.MAX_RECORD_ENTRIES + 1)]}
    with pytest.raises(chips.Invalid) as excinfo:
        chips._parse_record(json.dumps(too_many).encode())
    message = str(excinfo.value)
    assert "harness" in message
    assert str(chips.MAX_RECORD_ENTRIES) in message
    assert "entries" in message
    # The same sentence reaches a caller that hands the record to the store.
    with pytest.raises(chips.Invalid, match="harness"):
        chips._checked_record(too_many)


def test_code_is_stored_and_returned_byte_identically_and_never_run(tmp_path):
    """Stored code is data. What this test DOES prove, without executing anything:

    * the shell entry carries a payload that lands a real file at ``marker`` if any code
      path ever runs it through a shell, and the Python entry is *valid* Python whose
      first statements write the same ``marker`` (checked with ``ast.parse``, which only
      parses text and never compiles to a runnable object here);
    * both sources come back byte-identically from the in-memory record, from the
      ``record.json`` inside the ``.chip`` on disk, and from ``read_chip_record``;
    * ``marker`` does not exist before or after, so an injected
      ``subprocess.run(entry.source, shell=True)`` for ``language == "shell"`` — the exact
      regression that made this test pass vacuously — would create it and fail here.

    What it does NOT prove: that no code path could ever execute stored text in a way
    that leaves no trace on this filesystem (a network call, an in-memory effect). It is
    a tripwire on the payload that a shell or a Python ``exec`` would demonstrably run.
    """
    marker = tmp_path / "executed-marker"
    assert not marker.exists()
    shell_hostile = "#!/bin/sh\n: > " + str(marker) + "; $(whoami) `id`\n"
    python_hostile = (
        "import os, subprocess\n"
        f"open({str(marker)!r}, 'w').write('ran')\n"
        f"os.system('touch {marker}')\n"
        f"subprocess.run(['touch', {str(marker)!r}])\n"
        "print('$(touch x); `touch y`')\n"
    )
    # Parsed only, never compiled into a code object and never run.
    ast.parse(python_hostile)
    store = chips.ChipStore(tmp_path / "data")
    store.install_bytes(zip_bytes([("manifest.json", manifest_bytes())]))
    record = {"code": [{"name": "analysis.sh", "language": "shell", "source": shell_hostile,
                        "created_at": "t"},
                       {"name": "analysis.py", "language": "python", "source": python_hostile,
                        "created_at": "t"}]}
    store.write_record("foreign_pack", record)
    read_back = store.read_record("foreign_pack")["code"]
    assert read_back == record["code"]
    assert [row["source"] for row in read_back] == [shell_hostile, python_hostile]
    assert not marker.exists()

    # The bytes inside the archive are the same characters, unchanged.
    raw = store._target("foreign_pack").read_bytes()
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        on_disk = json.loads(archive.read("record.json").decode("utf-8"))
    assert on_disk["code"][0]["source"] == shell_hostile
    assert on_disk["code"][1]["source"] == python_hostile
    # Reading the record again out of the file on disk runs nothing either.
    assert chips.read_chip_record(raw).code[0].source == shell_hostile
    assert chips.read_chip_record(raw).code[1].source == python_hostile
    assert not marker.exists()


def members_of(path):
    """Every member of a chip file, read straight out of the archive on disk."""
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def test_installing_a_chip_that_carries_a_record_keeps_the_record(tmp_path):
    carried = {"harness": [{"key": "k", "value": "v"}]}
    raw = zip_bytes([("manifest.json", manifest_bytes()),
                     ("record.json", json.dumps(carried).encode())])
    store = chips.ChipStore(tmp_path / "data")
    store.install_bytes(raw)

    # Proven against the file on disk, independently of what install returned.
    installed = members_of(store._target("foreign_pack"))
    assert installed["record.json"] == chips.record_bytes(chips.Record.model_validate(carried))
    assert json.loads(installed["record.json"].decode("utf-8"))["harness"] == \
        chips.Record.model_validate(carried).model_dump(mode="json")["harness"]
    assert store.read_record("foreign_pack")["harness"] == \
        chips.Record.model_validate(carried).model_dump(mode="json")["harness"]


def manifest_bytes_at_version(version):
    """The same foreign pack at another version, for the version-replacing reinstall."""
    body = json.loads(manifest_bytes())
    body["pack"]["version"] = version
    return json.dumps(body).encode()


def test_reinstalling_a_different_version_preserves_the_stored_record(tmp_path):
    store = chips.ChipStore(tmp_path / "data")
    store.install_bytes(zip_bytes([("manifest.json", manifest_bytes())]))
    store.write_record("foreign_pack", full_record())
    stored = store.read_record("foreign_pack")
    assert stored == full_record()

    # A new version whose own archive carries no record must not wipe the notebook.
    store.install_bytes(zip_bytes([("manifest.json", manifest_bytes_at_version("0.2.0"))]))
    assert store.record("foreign_pack")["version"] == "0.2.0"
    assert store.read_record("foreign_pack") == full_record()
    on_disk = members_of(store._target("foreign_pack"))
    assert json.loads(on_disk["record.json"].decode("utf-8")) == full_record()


def test_a_malformed_record_at_install_is_refused_and_writes_nothing(tmp_path):
    raw = zip_bytes([("manifest.json", manifest_bytes()),
                     ("record.json", b'{"harness": [{"key": "k", "surprise": 1}]}')])
    store = chips.ChipStore(tmp_path / "data")
    with pytest.raises(chips.Invalid, match="does not match the chip record"):
        store.install_bytes(raw)
    # Refused, not silently dropped: no chip was written at all.
    assert not store._target("foreign_pack").exists()

    broken = zip_bytes([("manifest.json", manifest_bytes()), ("record.json", b"\xff\xfe not json")])
    with pytest.raises(chips.Invalid, match="not UTF-8 JSON"):
        store.install_bytes(broken)
    assert not store._target("foreign_pack").exists()


def test_write_record_refuses_an_over_cap_record_instance(tmp_path):
    store = chips.ChipStore(tmp_path / "data")
    store.install_bytes(zip_bytes([("manifest.json", manifest_bytes())]))
    too_many = chips.Record(questions=[chips.RecordQuestion(question_id=str(index))
                                       for index in range(chips.MAX_RECORD_ENTRIES + 1)])
    assert len(too_many.questions) == chips.MAX_RECORD_ENTRIES + 1
    with pytest.raises(chips.Invalid) as excinfo:
        store.write_record("foreign_pack", too_many)
    message = str(excinfo.value)
    assert "questions" in message
    assert str(chips.MAX_RECORD_ENTRIES) in message
    assert "entries" in message
    # The stored chip is untouched and still reads as a record read_record accepts.
    assert store.read_record("foreign_pack") == chips.Record().model_dump(mode="json")


def test_a_deeply_nested_record_is_refused_without_a_traceback(tmp_path, capsys):
    # 400 KB of "[[[[" fits under the 1 MB member cap and is not a decode error: the JSON
    # parser runs out of nesting first. It must be a refusal, never an uncaught traceback.
    nested = b"[" * 400_000
    assert len(nested) < chips.MAX_RECORD_BYTES
    with pytest.raises(chips.Invalid, match="nested too deeply"):
        chips._parse_record(nested)

    chip = tmp_path / "nested.chip"
    chip.write_bytes(zip_bytes([("manifest.json", manifest_bytes()),
                                ("record.json", nested)], zipfile.ZIP_STORED))
    with pytest.raises(chips.Invalid, match="nested too deeply"):
        chips.read_chip_record(chip.read_bytes())
    # The CLI keeps its exit-2 convention: no exit 1, no traceback on stderr.
    assert chips.main(["record", "show", "--chip", str(chip)]) == 2
    captured = capsys.readouterr()
    assert "nested too deeply" in captured.err
    assert "Traceback" not in captured.err
    # A JSON record file that is nested the same way is refused the same way.
    record_file = tmp_path / "nested.json"
    record_file.write_bytes(nested)
    assert chips.main(["record", "put", "--chip", str(chip), "--from", str(record_file)]) == 2
    assert "nested too deeply" in capsys.readouterr().err


def test_a_record_of_one_all_default_entry_writes_no_record_member(tmp_path):
    store = chips.ChipStore(tmp_path / "data")
    store.install_bytes(zip_bytes([("manifest.json", manifest_bytes())]))
    before = store._target("foreign_pack").read_bytes()

    assert store.write_record("foreign_pack", {"questions": [{}]}) == \
        chips.Record.model_validate({"questions": [{}]}).model_dump(mode="json")
    after = store._target("foreign_pack").read_bytes()
    assert set(members_of(store._target("foreign_pack"))) == {"manifest.json"}
    # The archive is the same bytes a record-free chip has: nothing meaningless was added.
    assert after == before


def test_record_cannot_be_written_to_a_builtin_id(tmp_path):
    store = chips.ChipStore(tmp_path / "data")
    with pytest.raises(chips.Invalid, match="built-in chip"):
        store.write_record("social_science", full_record())
    assert not (tmp_path / "data" / "chips" / "installed").exists()


def test_record_write_of_an_unknown_chip_is_a_key_error(tmp_path):
    store = chips.ChipStore(tmp_path / "data")
    with pytest.raises(KeyError):
        store.write_record("never_installed", full_record())
    with pytest.raises(KeyError):
        store.read_record("never_installed")


def test_record_write_cannot_escape_the_installed_directory(tmp_path):
    store = chips.ChipStore(tmp_path / "data")
    with pytest.raises(chips.Invalid, match="outside chips/installed"):
        store.write_record("../escape", full_record())


def test_a_builtin_chip_reads_an_empty_record(tmp_path):
    store = chips.ChipStore(tmp_path / "data")
    assert store.read_record("social_science") == full_record_empty()


def test_cli_shows_and_puts_a_record(tmp_path, capsys):
    out = tmp_path / "my_chip.chip"
    source = tmp_path / "my_chip.json"
    source.write_text(json.dumps(payload()), encoding="utf-8")
    assert chips.main(["build", "--from", str(source), "--out", str(out)]) == 0
    capsys.readouterr()

    # A chip without a record shows the empty record, not an error.
    assert chips.main(["record", "show", "--chip", str(out)]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown == full_record_empty()

    record_file = tmp_path / "record.json"
    record_file.write_text(json.dumps(full_record()), encoding="utf-8")
    assert chips.main(["record", "put", "--chip", str(out), "--from", str(record_file)]) == 0
    assert capsys.readouterr().out.strip() == str(out)

    assert chips.main(["record", "show", "--chip", str(out)]) == 0
    assert json.loads(capsys.readouterr().out) == full_record()
    # The rewritten chip is still a readable chip, with its manifest intact.
    manifest, art = chips.read_chip(out.read_bytes())
    assert manifest.pack.pack_id == "remote_sensing"
    assert art is None

    # --out writes elsewhere instead of rewriting the chip in place.
    copy = tmp_path / "with_record.chip"
    empty_file = tmp_path / "empty.json"
    empty_file.write_text("{}", encoding="utf-8")
    assert chips.main(["record", "put", "--chip", str(out), "--from", str(empty_file),
                       "--out", str(copy)]) == 0
    assert chips.read_chip_record(copy.read_bytes()) == chips.Record()
    assert chips.read_chip_record(out.read_bytes()) == chips.Record.model_validate(full_record())


def test_cli_reports_a_bad_record_without_traceback(tmp_path, capsys):
    out = tmp_path / "my_chip.chip"
    source = tmp_path / "my_chip.json"
    source.write_text(json.dumps(payload()), encoding="utf-8")
    assert chips.main(["build", "--from", str(source), "--out", str(out)]) == 0
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"code": [{"name": "x", "shell": "rm -rf /"}]}), encoding="utf-8")
    capsys.readouterr()
    assert chips.main(["record", "put", "--chip", str(out), "--from", str(bad)]) == 2
    stderr = capsys.readouterr().err
    assert "not a valid chip record" in stderr
    assert "code.0.shell" in stderr
    # The chip is untouched by a refused rewrite.
    assert chips.read_chip_record(out.read_bytes()) == chips.Record()


# ------------------------------------------------------------------ loss regressions
def record_with_sizeable_answers():
    """A record whose incoming member fits the cap but whose canonical form does not.

    The canonical form this module writes is indent=2, so it is larger than the compact
    bytes that arrive; 1400 answers of 650 characters is 1,006,613 bytes in and
    1,054,221 bytes out, against a 1,048,576 byte cap.
    """
    return {"answers": [{"value": "x" * 650} for _ in range(1400)]}


def test_install_refuses_a_record_whose_canonical_form_would_be_over_the_cap(tmp_path, monkeypatch):
    # The shipped record cap is 100 MB, so the rule is pinned against a lowered cap: the
    # canonical form is what is re-checked, and it grows past the member the caller sent.
    monkeypatch.setattr(chips, "MAX_RECORD_BYTES", 1024 * 1024)
    record = record_with_sizeable_answers()
    member = json.dumps(record, separators=(",", ":")).encode()
    canonical = chips.record_bytes(chips.Record.model_validate(record))
    assert len(member) < chips.MAX_RECORD_BYTES < len(canonical)
    raw = zip_bytes([("manifest.json", manifest_bytes()), ("record.json", member)],
                    zipfile.ZIP_STORED)
    store = chips.ChipStore(tmp_path / "data")

    with pytest.raises(chips.Invalid) as excinfo:
        store.install_bytes(raw)
    message = str(excinfo.value)
    assert str(len(canonical)) in message
    assert "1 MB" in message

    # Refused before anything was written: no chip, no .part, not even the folder.
    assert not store.installed.exists()
    assert list(store.installed.glob("*")) == []


def test_install_route_refuses_an_over_canonical_record_as_400_and_writes_nothing(client, tmp_path,
                                                                                monkeypatch):
    monkeypatch.setattr(chips, "MAX_RECORD_BYTES", 1024 * 1024)
    record = record_with_sizeable_answers()
    member = json.dumps(record, separators=(",", ":")).encode()
    assert len(member) < chips.MAX_RECORD_BYTES
    raw = zip_bytes([("manifest.json", manifest_bytes()), ("record.json", member)],
                    zipfile.ZIP_STORED)
    response = client.post("/chips/install", json={"base64": base64.b64encode(raw).decode()})
    assert response.status_code == 400, response.json()
    body = response.json()
    assert body["error"] == "invalid_chip"
    assert "record" in body["message"]
    assert "1 MB" in body["message"]
    # The route that answers 400 must not leave the chip behind: it is not listed and
    # no file exists for it anywhere in the store.
    assert "foreign_pack" not in {row["pack_id"] for row in client.get("/chips").json()["chips"]}
    installed = tmp_path / "data" / "chips" / "installed"
    assert list(installed.glob("foreign_pack*")) == []


def replace_member(path, name, data):
    """Rewrites one member of a chip on disk and returns the new bytes, for damage tests."""
    with zipfile.ZipFile(io.BytesIO(path.read_bytes())) as archive:
        members = [(member, archive.read(member)) for member in archive.namelist()]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for member, payload in members:
            archive.writestr(member, data if member == name else payload)
    path.write_bytes(buffer.getvalue())
    return path.read_bytes()


def test_a_stored_chip_with_an_unreadable_record_stays_visible_and_untouchable(tmp_path):
    store = chips.ChipStore(tmp_path / "data")
    store.install_bytes(zip_bytes([("manifest.json", manifest_bytes())]))
    store.write_record("foreign_pack", full_record())
    path = store._target("foreign_pack")
    damaged = replace_member(path, "record.json", b'{"harness": [{"key": "k", "surprise": 1}]}')

    # The chip is still a chip: it lists, it reads, and the damage is named.
    listed = {row["pack_id"]: row for row in store.list()["chips"]}
    assert "foreign_pack" in listed
    assert "does not match the chip record" in listed["foreign_pack"]["record_error"]
    assert store.record("foreign_pack")["pack"]["pack_id"] == "foreign_pack"
    # Only the record route reports the damage; it never 404s the chip.
    with pytest.raises(chips.Invalid, match="foreign_pack is stored with a record"):
        store.read_record("foreign_pack")

    # An existing chip still counts as existing: the same version is refused.
    with pytest.raises(chips.Invalid, match="already installed"):
        store.install_bytes(zip_bytes([("manifest.json", manifest_bytes())]))
    assert path.read_bytes() == damaged

    # A version-replacing chip that carries no record cannot carry the damaged one
    # forward, so it is refused instead of overwriting the researcher's file.
    with pytest.raises(chips.Invalid, match="cannot read"):
        store.install_bytes(zip_bytes([("manifest.json", manifest_bytes_at_version("0.2.0"))]))
    assert path.read_bytes() == damaged

    # A version-replacing chip that carries its own record would also overwrite bytes
    # this build cannot put back, so it is refused the same way.
    with pytest.raises(chips.Invalid, match="export or delete"):
        store.install_bytes(zip_bytes([("manifest.json", manifest_bytes_at_version("0.2.0")),
                                       ("record.json", json.dumps(full_record()).encode())]))
    assert path.read_bytes() == damaged

    # Export still hands the file out byte for byte, which is the recovery path.
    export = store.export("foreign_pack")
    assert base64.b64decode(export["base64"]) == damaged


def test_a_deeply_nested_manifest_is_refused_without_a_traceback(client, tmp_path):
    # 200 KB of "[[[[" fits under the 256 KB manifest cap and is not a decode error: the
    # JSON parser runs out of nesting first. It must be a 400, never a 500.
    nested = b"[" * 200_000
    assert len(nested) < chips.MAX_MANIFEST_BYTES
    with pytest.raises(chips.Invalid, match="nested too deeply"):
        chips._parse_manifest(nested)

    chip = tmp_path / "nested_manifest.chip"
    chip.write_bytes(zip_bytes([("manifest.json", nested)], zipfile.ZIP_STORED))
    with pytest.raises(chips.Invalid, match="nested too deeply"):
        chips.read_chip(chip.read_bytes())

    response = client.post("/chips/install", json={"base64": base64.b64encode(chip.read_bytes()).decode()})
    assert response.status_code == 400, response.json()
    assert "nested too deeply" in response.json()["message"]
