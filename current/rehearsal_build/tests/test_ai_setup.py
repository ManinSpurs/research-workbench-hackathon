"""Minimal ai_setup CLI helper tests: file creation / no-overwrite, no network."""
from __future__ import annotations

import os
import stat
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import ai_setup


def _patch_secret_path(monkeypatch: pytest.MonkeyPatch, path: Path) -> None:
    monkeypatch.setattr(ai_setup, "secret_path", lambda: path)


def test_no_overwrite_when_key_file_exists(monkeypatch, tmp_path, capsys):
    fake = tmp_path / "meta_api_key"
    fake.write_text("original-key")
    _patch_secret_path(monkeypatch, fake)

    def _fail_on_call(prompt=""):
        raise AssertionError("getpass must not be called when key file exists")

    monkeypatch.setattr(ai_setup, "getpass", _fail_on_call)
    assert ai_setup.main() is None
    assert fake.read_text() == "original-key"
    assert "No overwrite" in capsys.readouterr().out


def test_saves_new_key_without_network(monkeypatch, tmp_path, capsys):
    fake = tmp_path / "sub" / "meta_api_key"
    _patch_secret_path(monkeypatch, fake)
    monkeypatch.setattr(ai_setup, "getpass", lambda prompt="": "dummy-key-123")
    ai_setup.main()
    assert fake.read_text() == "dummy-key-123"
    assert stat.S_IMODE(os.lstat(fake).st_mode) == 0o600
    out = capsys.readouterr().out
    assert "Saved" in out
    assert "No API request was sent" in out


@pytest.mark.parametrize("bad", ["", "   ", "has space", "tab\there", "new\nline"])
def test_rejects_empty_or_whitespace_key(monkeypatch, tmp_path, bad):
    fake = tmp_path / "meta_api_key"
    _patch_secret_path(monkeypatch, fake)
    monkeypatch.setattr(ai_setup, "getpass", lambda prompt="": bad)
    with pytest.raises(SystemExit):
        ai_setup.main()
    assert not fake.exists()
