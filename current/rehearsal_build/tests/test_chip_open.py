"""Opening a notebook is not permission to overwrite another same-version file."""
import base64
from pathlib import Path

from app import chips
from test_chips import client, payload  # noqa: F401


def test_exact_reopen_preserves_file_and_install_still_refuses(client):
    built = client.post("/chips/build", json=payload()).json()
    assert client.post("/chips/open", json=built_file(built)).status_code == 200
    exported = client.post("/chips/export/remote_sensing").json()
    original = client.get("/chips/remote_sensing").json()
    target = Path(original["path"])
    before = target.read_bytes()
    assert client.post("/chips/open", json=exported).status_code == 200
    assert target.read_bytes() == before
    assert client.post("/chips/install", json=exported).status_code == 400


def built_file(result):
    return {"base64": result["base64"], "filename": result["filename"]}


def test_different_same_version_refused_without_overwriting(client):
    first = client.post("/chips/build", json=payload()).json()
    assert client.post("/chips/open", json=built_file(first)).status_code == 200
    before = client.post("/chips/export/remote_sensing").json()
    changed = client.post("/chips/build", json=payload(title="Different notebook")).json()
    response = client.post("/chips/open", json=built_file(changed))
    assert response.status_code == 400
    assert "already installed" in response.json()["message"]
    assert client.post("/chips/export/remote_sensing").json() == before


def test_builtin_open_validates_actual_bytes(client):
    raw = chips.builtin_chip("social_science")
    response = client.post("/chips/open", json={"base64": base64.b64encode(raw).decode()})
    assert response.status_code == 200 and response.json()["source"] == "builtin"
    manifest, art = chips.read_chip(raw)
    changed = chips._encode(manifest, art, conversation="Not a shipped notebook")
    response = client.post("/chips/open", json={"base64": base64.b64encode(changed).decode()})
    assert response.status_code == 400
