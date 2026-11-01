"""Packet export scales to provider counts without exposing identities."""
import json

import pytest

from app.ai import comparison_run as runner


@pytest.mark.parametrize("providers", [1, 3, 15])
def test_finish_preserves_all_provider_arms_and_unblinds(tmp_path, providers):
    results = [
        {"model": "provider-%s" % n,
         "outputs": {arm: {"result": {"answer": "Estimated cost", "claims": []}}
                     for arm in ("basic", "harness")}}
        for n in range(providers)
    ]
    codes = ["BLIND-%02d" % n for n in reversed(range(providers * 2))]
    plan = {"blind_codes": {"FX-COST-1": codes},
            "routes": {str(n): {"questions": [{}]} for n in range(providers)}}
    summaries = [{"error_code": None} for _ in results]
    summary = runner.finish(tmp_path, plan, results, ["FX-COST-1"] * providers,
                            summaries, 0)
    packet = json.loads((tmp_path / "blind-scoring-packet.json").read_text())
    key = json.loads((tmp_path / "unblinding-key.json").read_text())
    arms = packet["cases"][0]["blinded_arms"]
    assert len(arms) == providers * 2
    assert {arm["blind_code"] for arm in arms} == set(codes)
    mapping = key["cases"]["FX-COST-1"]["mapping"]
    assert {entry["job_index"] for entry in mapping.values()} == set(range(providers))
    assert all("run_params" not in arm for arm in arms)
    assert summary["all_jobs_completed"] is True
