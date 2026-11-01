import re
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import chips
from app.api import create_app
from app.dev_history_chip import PACK_ID, dev_history_bytes


@pytest.mark.parametrize("lang, pack_id", [("en", PACK_ID), ("ko", PACK_ID + "_ko")])
def test_dev_history_chip_imports_as_a_briefing_newest_first(tmp_path, lang, pack_id):
    raw = dev_history_bytes(lang)
    assert not re.search(rb"/" rb"Users/|/home/|sk-|wk-|ws-|api_key", raw)
    data = tmp_path / "data"
    shutil.copytree(Path(__file__).resolve().parents[1] / "data", data)
    chips.ChipStore(data).install_bytes(raw)
    with TestClient(create_app(db_path=str(tmp_path / "test.db"), data_dir=data)) as c:
        response = c.post(f"/chips/{pack_id}/import", json={"memory": "remember"})
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["model_calls"] == 0
        again = c.post(f"/chips/{pack_id}/import", json={"memory": "remember"}).json()
        assert again["project_id"] == body["project_id"]
    record = chips.read_chip_record(raw)
    assert [q.asked_at for q in record.questions] == sorted((q.asked_at for q in record.questions), reverse=True)
    answered = {a.question_id for a in record.answers}
    assert [q.question_id for q in record.questions if q.question_id not in answered] == ["human-review", "release"]
