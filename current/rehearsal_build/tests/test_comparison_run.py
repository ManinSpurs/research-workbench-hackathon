"""Comparison CLI contract over mock HTTP/process boundaries, never inference."""
import contextlib
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace

import httpx
import pytest

from app.ai import comparison_run as runner, local_launcher, local_runtime, service
from app.ai.provider import BasetenProvider, LocalProvider, ProviderError
from tools.rehearse_fresh_comparison import CASES


@pytest.fixture
def wired(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "LOGS", tmp_path / "logs")
    monkeypatch.setattr(runner.tempfile, "tempdir", str(tmp_path))
    monkeypatch.setattr(service.secrets, "baseten_key", lambda: "mock-baseten-key")
    monkeypatch.setattr(runner.local_config, "read", lambda: {
        "runtime_executable": "/mock/llama-server", "model_weights": "/mock/model.gguf",
        "alias": local_runtime.DEFAULT_WIRE_MODEL, "context": 8192, "port": 9931,
        "base_url": "http://127.0.0.1:9931"})
    launches, stops, inference = [], [], []
    control = {"incomplete_first": False}

    def start(server):
        launches.append(server)
        server.process = SimpleNamespace(pid=os.getpid())
        server.secret = "mock-local-bearer"
        server.run_id = "mock-run-%d" % len(launches)
        server.weights_sha256 = local_runtime.MODEL_FACTS["sha256"]
        server.template_overhead_tokens = 16
        server.observed_identity = {"model_alias": server.config["alias"],
                                    "n_ctx": server.config["context"], "n_ctx_train": 262144}

    monkeypatch.setattr(local_launcher.LocalServer, "start", start)
    monkeypatch.setattr(local_launcher.LocalServer, "stop", lambda server: stops.append(server))

    def handler(req):
        if req.url.host != "inference.baseten.co" and req.headers.get("authorization") != "Bearer mock-local-bearer":
            return httpx.Response(401, json={"error": "unauthorized"})
        if req.url.path.endswith("/models"):
            model = runner.BASETEN_MODEL_ID if req.url.host == "inference.baseten.co" else local_runtime.DEFAULT_WIRE_MODEL
            return httpx.Response(200, json={"data": [{"id": model}]})
        if req.url.path == local_runtime.PROPS_PATH:
            cfg = local_runtime.read_config()
            return httpx.Response(200, json={"model_alias": cfg["wire_model"], "n_ctx_train": 262144,
                "total_slots": 1, "default_generation_settings": {"n_ctx": cfg["context"]}})
        body = json.loads(req.content)
        if req.url.path == local_runtime.APPLY_TEMPLATE_PATH:
            return httpx.Response(200, json={"prompt": body["messages"][0]["content"]})
        if req.url.path == local_runtime.TOKENIZE_PATH:
            return httpx.Response(200, json={"tokens": body["content"].split()})
        inference.append(req)
        if control["incomplete_first"] and req.url.host == "inference.baseten.co":
            control["incomplete_first"] = False
            return httpx.Response(200, json={"status": "incomplete", "model": runner.BASETEN_MODEL_ID,
                "output": [], "usage": {"input_tokens": 200, "output_tokens": 100}})
        text = json.dumps(body)
        case = next(case for case in CASES if case.question in text)
        answer = json.dumps(case.frame if "public work plan" in text else case.result)
        if req.url.path.endswith("/responses"):
            return httpx.Response(200, json={"status": "completed", "model": body["model"],
                "output": [{"type": "message", "content": [{"type": "output_text", "text": answer}]}],
                "usage": {"input_tokens": 200, "output_tokens": 100}})
        assert req.url.path == local_runtime.CHAT_PATH
        return httpx.Response(200, json={"model": local_runtime.DEFAULT_WIRE_MODEL,
            "choices": [{"finish_reason": "stop", "message": {"role": "assistant", "content": answer}}],
            "usage": {"prompt_tokens": 200, "completion_tokens": 100, "total_tokens": 300}})

    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(service, "BasetenProvider", lambda: BasetenProvider(
        transport, key_reader=lambda: "mock-baseten-key"))
    monkeypatch.setattr(service, "LocalProvider", lambda *args, **kwargs: LocalProvider(
        *args, **kwargs, transport=transport))
    return launches, stops, inference, control


def test_prepare_then_hash_bound_execute_saves_blind_packet(wired):
    launches, stops, inference, _ = wired
    summary = runner.prepare(CASES)
    assert inference == []
    assert summary["paid_calls"] == 9 and summary["local_calls"] == 9
    assert summary["maximum_reserved_usd"] == pytest.approx(11.874816)
    path = Path(summary["plan_path"])
    plan = json.loads(path.read_text())["plan"]
    for route in ("baseten", "local"):
        assert sum(len(j["calls"]) for j in plan["routes"][route]["jobs"]) == 9
        assert [j["content"]["execution_order"] for j in plan["routes"][route]["jobs"]] == [
            ["basic", "harness"], ["harness", "basic"], ["basic", "harness"]]
    assert len(stops) == len(launches) == 1
    with pytest.raises(ValueError, match="plan_hash_mismatch"):
        runner.execute(path, "wrong", summary["content_hashes"])
    with pytest.raises(ValueError, match="content_hashes_mismatch"):
        runner.execute(path, summary["plan_hash"], ["wrong"])
    assert inference == []
    result = runner.execute(path, summary["plan_hash"], summary["content_hashes"])
    assert len(inference) == 18
    assert result["cumulative_calculated_usd"] == pytest.approx(0.006075)
    assert len(result["jobs"]) == 6
    blind = json.loads(Path(result["packet_path"]).read_text())
    assert "unblinding_key" not in blind
    assert "meta-models/muse-spark-open" not in json.dumps(blind)
    assert "execution_order_blinded" not in json.dumps(blind)
    assert (path.parent / "unblinding-key.json").is_file()
    assert len(stops) == len(launches) == 2
    with pytest.raises(FileExistsError):
        runner.execute(path, summary["plan_hash"], summary["content_hashes"])
    assert len(inference) == 18


def pin_mock_execution(monkeypatch, prior):
    # Only this test's completed mock DB is trusted, before any tampering.
    with (prior / "local-execution.db").open("rb") as source:
        digest = hashlib.file_digest(source, "sha256").hexdigest()
    monkeypatch.setattr(runner, "PINNED_REUSE_DB_SHA256", frozenset({digest}))


def test_cloud_only_rerun_reuses_local_sheets_and_counts_prior_spend(wired, monkeypatch):
    launches, _, inference, _ = wired
    first = runner.prepare(CASES)
    run1 = runner.execute(first["plan_path"], first["plan_hash"], first["content_hashes"])
    pin_mock_execution(monkeypatch, Path(first["plan_path"]).parent)
    before = len(inference)
    run2 = runner.prepare(CASES, Path(first["plan_path"]).parent)
    assert run2["local_calls"] == 0 and len(run2["content_hashes"]) == 3
    assert len(inference) == before
    result = runner.execute(run2["plan_path"], run2["plan_hash"], run2["content_hashes"])
    assert len(inference) == before + 9
    assert len(launches) == 2
    assert result["cumulative_calculated_usd"] == pytest.approx(
        run1["cumulative_calculated_usd"] + 0.006075)
    assert [j["provider"] for j in result["jobs"]] == ["baseten"] * 3 + ["local_reused"] * 3
    blind = json.loads(Path(result["packet_path"]).read_text())
    assert all(len(case["blinded_arms"]) == 4 for case in blind["cases"])


def test_spend_guard_stops_on_single_and_cumulative_thresholds(tmp_path):
    single = runner.CostStop(tmp_path)
    with pytest.raises(ProviderError, match="single_call_over_1"):
        single({"cost_usd": 1.000001})
    cumulative = runner.CostStop(tmp_path)
    for _ in range(5):
        cumulative({"cost_usd": 1})
    assert cumulative.total == 5
    with pytest.raises(ProviderError, match="cumulative_over_5"):
        cumulative({"cost_usd": 0.000001})


def test_reused_output_tamper_rejected_before_paid_work(wired, monkeypatch):
    _, _, inference, _ = wired
    first = runner.prepare(CASES)
    runner.execute(first["plan_path"], first["plan_hash"], first["content_hashes"])
    prior = Path(first["plan_path"]).parent
    pin_mock_execution(monkeypatch, prior)
    second = runner.prepare(CASES, prior)
    before = len(inference)
    path = prior / "local-FX-COST-1-output.json"
    result = json.loads(path.read_text())
    result["outputs"]["basic"]["result"]["answer"] = "tampered"
    runner.save(path, result)
    with pytest.raises(ValueError, match="reuse_provenance_invalid: output_mismatch"):
        runner.prepare(CASES, prior)
    # Also reject a self-consistently rehashed approved plan with altered reuse.
    packet = json.loads(Path(second["plan_path"]).read_text())
    reuse = packet["plan"]["reused_jobs"][0]
    reuse["result"] = result
    reuse["result_hash"] = service.digest(result)
    packet["plan_hash"] = service.digest(packet["plan"])
    runner.save(Path(second["plan_path"]), packet)
    with pytest.raises(ValueError, match="reuse_provenance_invalid: output_mismatch"):
        runner.execute(second["plan_path"], packet["plan_hash"], second["content_hashes"])
    assert len(inference) == before


def test_reused_receipt_tamper_rejected_before_any_transport(wired, monkeypatch):
    first = runner.prepare(CASES)
    runner.execute(first["plan_path"], first["plan_hash"], first["content_hashes"])
    prior = Path(first["plan_path"]).parent
    pin_mock_execution(monkeypatch, prior)
    second = runner.prepare(CASES, prior)
    packet_path = Path(second["plan_path"])
    packet = json.loads(packet_path.read_text())
    reused = packet["plan"]["reused_jobs"][0]
    # Keep the independently verified output and its hash intact.
    forged_receipt = {**reused["receipt"],
                      "model": runner.BASETEN_MODEL_ID, "provider": "baseten",
                      "calculated_cost_usd": 123456, "reservation_usd": 123456,
                      "stages": [], "created_at": "forged"}
    reused["receipt"] = forged_receipt
    packet["plan_hash"] = service.digest(packet["plan"])
    runner.save(packet_path, packet)
    # Replace the HTTP boundary only after creating valid run-1 evidence.
    calls = []

    def forbidden_transport(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Receipt rejection must happen before any HTTP request")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_transport)
    with pytest.raises(ValueError, match="reuse_provenance_invalid: receipt_mismatch"):
        runner.execute(packet_path, packet["plan_hash"], second["content_hashes"])
    runner.save(prior / "local-FX-COST-1-receipt.json", forged_receipt)
    with pytest.raises(ValueError, match="reuse_provenance_invalid: receipt_mismatch"):
        runner.prepare(CASES, prior)
    assert calls == []
    assert not (packet_path.parent / "execute-started").exists()


def test_substituted_reuse_db_rejected_before_any_transport(wired, monkeypatch):
    first = runner.prepare(CASES)
    runner.execute(first["plan_path"], first["plan_hash"], first["content_hashes"])
    prior = Path(first["plan_path"]).parent
    pin_mock_execution(monkeypatch, prior)
    second = runner.prepare(CASES, prior)
    substitute = prior.parent / "substituted-run1"
    shutil.copytree(prior, substitute)
    # Forge a self-consistent DB/receipt pair, leaving the stored result intact.
    with contextlib.closing(sqlite3.connect(substitute / "local-execution.db")) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT id,request_json FROM ai_jobs ORDER BY created_at LIMIT 1").fetchone()
        record = json.loads(row["request_json"])
        record["model"] = runner.BASETEN_MODEL_ID
        record["provider"] = "baseten"
        db.execute("UPDATE ai_jobs SET request_json=?,preview_hash=?,reservation=? WHERE id=?",
                   (service.dumps(record), service.digest(record), 123456, row["id"]))
        forged_receipt = service.AIService.receipt_from_db(db, row["id"])
        db.commit()
        # The attack replaces the DB file itself, not merely its WAL sidecar.
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    runner.save(substitute / "local-FX-COST-1-receipt.json", forged_receipt)
    path = Path(second["plan_path"])
    packet = json.loads(path.read_text())
    for reused in packet["plan"]["reused_jobs"]:
        reused["source"] = str(substitute)
    packet["plan"]["reused_jobs"][0]["receipt"] = forged_receipt
    packet["plan_hash"] = service.digest(packet["plan"])
    runner.save(path, packet)
    calls = []

    def forbidden_transport(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("DB substitution rejection must precede any HTTP request")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_transport)
    with pytest.raises(ValueError, match="^reuse_provenance_invalid: source_db_unpinned$"):
        runner.execute(path, packet["plan_hash"], second["content_hashes"])
    with pytest.raises(ValueError, match="^reuse_provenance_invalid: source_db_unpinned$"):
        runner.prepare(CASES, substitute)
    assert calls == []
    assert not (path.parent / "execute-started").exists()


def test_reuse_db_swapped_after_hashing_is_not_queried(wired, monkeypatch):
    first = runner.prepare(CASES)
    runner.execute(first["plan_path"], first["plan_hash"], first["content_hashes"])
    prior = Path(first["plan_path"]).parent
    pin_mock_execution(monkeypatch, prior)
    second = runner.prepare(CASES, prior)
    forged_dir = prior.parent / "forged-run1"
    shutil.copytree(prior, forged_dir)
    with contextlib.closing(sqlite3.connect(forged_dir / "local-execution.db")) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("SELECT id,request_json FROM ai_jobs ORDER BY created_at LIMIT 1").fetchone()
        record = json.loads(row["request_json"])
        record["model"] = runner.BASETEN_MODEL_ID
        record["provider"] = "baseten"
        db.execute("UPDATE ai_jobs SET request_json=?,preview_hash=?,reservation=? WHERE id=?",
                   (service.dumps(record), service.digest(record), 123456, row["id"]))
        forged_receipt = service.AIService.receipt_from_db(db, row["id"])
        db.commit()
        db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    forged_bytes = (forged_dir / "local-execution.db").read_bytes()
    genuine = prior / "local-execution.db"
    real_read_bytes = Path.read_bytes

    def swap_after_hash_read(self):
        data = real_read_bytes(self)
        if self == genuine:
            # The race: the pinned bytes are read, then the path is replaced.
            genuine.write_bytes(forged_bytes)
        return data

    monkeypatch.setattr(Path, "read_bytes", swap_after_hash_read)
    path = Path(second["plan_path"])
    packet = json.loads(path.read_text())
    packet["plan"]["reused_jobs"][0]["receipt"] = forged_receipt
    packet["plan_hash"] = service.digest(packet["plan"])
    runner.save(path, packet)
    calls = []

    def forbidden_transport(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("A swapped DB must be rejected before any HTTP request")

    monkeypatch.setattr(httpx.AsyncClient, "send", forbidden_transport)
    with pytest.raises(ValueError, match="reuse_provenance_invalid: receipt_mismatch"):
        runner.execute(path, packet["plan_hash"], second["content_hashes"])
    assert calls == []
    assert not (path.parent / "execute-started").exists()


def test_unknown_charge_blocks_that_provider_but_other_providers_continue(wired):
    _, _, inference, control = wired
    control["incomplete_first"] = True
    summary = runner.prepare(CASES)
    result = runner.execute(summary["plan_path"], summary["plan_hash"], summary["content_hashes"])
    assert len(inference) == 10  # one failed cloud call, nine local
    assert len(result["jobs"]) == 6
    assert result["jobs"][0]["error_code"] == "response_not_completed"
    assert all(j["error_code"] == "provider_prior_unknown_charge" for j in result["jobs"][1:3])
    assert all(j["error_code"] is None for j in result["jobs"][3:])
    packet = json.loads(Path(result["packet_path"]).read_text())
    assert len(packet["cases"]) == 3
    assert {c["case_id"]: len(c["blinded_arms"]) for c in packet["cases"]} == {
        "FX-COST-1": 2, "FX-APPROVAL-1": 2, "NYC-DATE-1": 2}
