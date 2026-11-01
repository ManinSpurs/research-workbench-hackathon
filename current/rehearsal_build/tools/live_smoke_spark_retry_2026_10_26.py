"""ONE fresh Spark live call (retry after row-1 response_not_completed halt).

Authorization: exactly one Spark reasoning call, max_output_tokens=2048,
reservation $1.319424, stop if calculated cost > $0.05. Rows 2-5 NOT
authorized. The halted job ai-fd3068fb1d58 is never retried. No raw HTTP,
no key logging (only SET/UNSET is ever printed).
"""
import asyncio
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app.harness import Harness  # noqa: E402
from app.offline import OfflineAdapter  # noqa: E402
from app.ai.service import (  # noqa: E402
    AIService,
    SAFE_CORE,
    FRAME_CONTRACT,
    FINAL_CONTRACT,
    WIRE_VERSION,
    digest,
)
from app.harness import Conflict  # noqa: E402
from app.ai.provider import ProviderError  # noqa: E402

OUT = ROOT.parent / "logs" / "LIVE_SMOKE_SPARK_RETRY_2026-10-26"
TRIP = 0.05
RESERVATION = 1.319424


def jdefault(o):
    return str(o)


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    h = Harness(str(OUT / "scratch_retry_qa.db"), adapter=OfflineAdapter())
    svc = AIService(h)
    report = {
        "key": None,
        "catalog_ok": False,
        "settings_before": None,
        "settings_after": None,
        "job": None,
        "halt": None,
        "cloud_off_at_end": False,
    }
    try:
        from app.ai import secrets as secrets_mod

        report["key"] = "SET" if secrets_mod.key() else "UNSET"
        if report["key"] != "SET":
            report["halt"] = "key UNSET; stopped before any send"
            return finish(svc, h, report)

        report["settings_before"] = svc.settings()
        svc.configure(True, 150)
        after_enable = svc.settings()
        need = RESERVATION + report["settings_before"]["local_committed_or_reserved_usd"]
        if after_enable["local_allowance_remaining_usd"] < need:
            report["halt"] = "allowance insufficient for $1.319424 reservation; stopped"
            return finish(svc, h, report)

        catalog = await svc.verify_account()
        (OUT / "catalog.json").write_text(
            json.dumps(
                {"verified_at": catalog["verified_at"], "catalog": catalog["catalog"],
                 "credit_balance": catalog["credit_balance"]},
                indent=2,
                default=jdefault,
            )
        )
        if "muse-spark-1.3" not in catalog["catalog"]:
            report["halt"] = "catalog missing muse-spark-1.3; stopped before any send"
            return finish(svc, h, report)
        report["catalog_ok"] = True

        pid = h.register_project("fx")["project_id"]
        q = await h.create_question(
            pid, "Synthetic check: what does est_cost measure in this retry fixture?"
        )
        qid = q["question_id"]
        body = {
            "task": "research_query",
            "model": "muse-spark-1.3",
            "text": "In one sentence, define plan-review completion. Use only the provided context.",
            "harness": False,
            "compare": False,
            "max_output_tokens": 2048,
            "max_cost_usd": 5.0,
        }
        entry = {"model": "muse-spark-1.3", "task": "research_query",
                 "question_id": qid}
        try:
            preview = svc.prepare(qid, body)
            entry["job_id"] = preview["job_id"]
            entry["preview_hash"] = preview["preview_hash"]
            entry["reservation"] = preview["maximum_reserved_usd"]
            if preview["model"] != "muse-spark-1.3":
                raise RuntimeError(f"preview model {preview['model']} != muse-spark-1.3")
            if preview.get("data_use_mode") != "Standard":
                raise RuntimeError("non-Standard data_use_mode in preview")
            if not preview.get("model_access_verified"):
                raise RuntimeError("model_access_verified False in preview")
            tx = preview.get("transmission", {})
            if tx.get("destination") != "https://api.meta.ai/v1":
                raise RuntimeError("unexpected preview destination")
            if abs(preview["maximum_reserved_usd"] - RESERVATION) > 1e-9:
                raise RuntimeError(
                    f"reservation {preview['maximum_reserved_usd']} != {RESERVATION}"
                )
            started = time.perf_counter()
            result = await svc.execute(preview["job_id"], preview["preview_hash"], True)
            entry["elapsed_seconds"] = time.perf_counter() - started
            entry["status"] = "completed"
        except (Conflict, ProviderError, RuntimeError, ValueError) as exc:
            entry["elapsed_seconds"] = entry.get("elapsed_seconds")
            entry["status"] = "failed"
            entry["error"] = getattr(exc, "code", None) or str(exc)[:200]
            try:
                result = svc.receipt(preview["job_id"]).get("result")
            except Exception:
                result = None
            if result is None:
                row = svc.db.execute(
                    "SELECT result_json FROM ai_jobs WHERE id=?", (preview["job_id"],)
                ).fetchone()
                result = json.loads(row[0]) if row and row[0] else None
        entry["result"] = result
        usage_rows = [
            dict(r) for r in svc.db.execute(
                "SELECT * FROM ai_usage WHERE job_id=?", (preview["job_id"],)
            )
        ]
        for u in usage_rows:
            u["accounting_json"] = json.loads(u["accounting_json"])
        entry["usage_rows"] = usage_rows
        entry["spend_row"] = dict(
            svc.db.execute(
                "SELECT * FROM ai_spend WHERE job_id=?", (preview["job_id"],)
            ).fetchone()
        )
        entry["shape"] = [
            {k: u["accounting_json"].get(k) for k in (
                "response_keys", "response_status",
                "response_output_count", "response_output_types")}
            for u in usage_rows
        ]
        receipt = svc.receipt(preview["job_id"])
        (OUT / "job_1_spark_retry.json").write_text(
            json.dumps(
                {"model": "muse-spark-1.3", "task": "research_query",
                 "question_id": qid, "preview_ref": {
                     "job_id": preview["job_id"],
                     "preview_hash": preview["preview_hash"],
                     "model": preview["model"],
                     "reservation": preview["maximum_reserved_usd"],
                     "destination": preview.get("transmission", {}).get("destination"),
                     "data_use_mode": preview.get("data_use_mode")},
                 "result": result, "usage_rows": usage_rows,
                 "spend_row": entry["spend_row"]},
                indent=2,
                default=jdefault,
            )
        )
        (OUT / f"receipt_{preview['job_id']}.json").write_text(
            json.dumps(receipt, indent=2, default=jdefault)
        )
        entry["receipt_file"] = f"receipt_{preview['job_id']}.json"
        entry["calculated_cost_usd"] = (
            result.get("calculated_cost_usd") if result else None
        )
        if entry["status"] == "completed":
            if entry["calculated_cost_usd"] is None or not (
                    result.get("usage_complete")):
                entry["status"] = "completed_unknown_charge"
                report["halt"] = "usage missing/unparseable on success path"
            elif entry["calculated_cost_usd"] > TRIP:
                report["halt"] = (
                    f"cost {entry['calculated_cost_usd']} above $0.05 trip line"
                )
        else:
            report["halt"] = f"call failed: {entry['error']}"
        report["job"] = entry
        return finish(svc, h, report)
    finally:
        try:
            h.db.close()
        except Exception:
            pass


def finish(svc, h, report) -> int:
    try:
        svc.configure(False, svc.settings()["budget_usd"])
        report["settings_after"] = svc.settings()
        report["cloud_off_at_end"] = not report["settings_after"]["cloud_enabled"]
    except Exception as exc:  # noqa: BLE001
        report["cloud_off_at_end"] = False
        report["halt"] = (report.get("halt") or "") + f" [close-out error: {exc}]"
    report["contract_fingerprint"] = digest(
        [SAFE_CORE, FRAME_CONTRACT, FINAL_CONTRACT, WIRE_VERSION]
    )
    (OUT / "settings_before.json").write_text(
        json.dumps(report.get("settings_before"), indent=2, default=jdefault)
    )
    (OUT / "settings_after.json").write_text(
        json.dumps(report.get("settings_after"), indent=2, default=jdefault)
    )
    job = report.get("job") or {}
    lines = [
        "# Live smoke summary (SPARK RETRY, 2026-10-26)",
        "",
        f"Key gate: {report['key']}; catalog_ok: {report['catalog_ok']}",
        f"DB: `{OUT / 'scratch_retry_qa.db'}` (new isolated scratch, synthetic FX only)",
        "",
        f"job_id: {job.get('job_id')}",
        f"status: {job.get('status')}",
        f"usage: {json.dumps([u.get('accounting_json', {}).get('usage') for u in job.get('usage_rows', [])], default=jdefault)}",
        f"calculated cost vs $0.05 line: {job.get('calculated_cost_usd')}",
        f"elapsed s: {job.get('elapsed_seconds')}",
        f"envelope shape: {json.dumps(job.get('shape'), default=jdefault)}",
        "",
        f"Halt/completion: {report['halt'] or 'completed within trip line'}",
        f"Cloud off at end: {report['cloud_off_at_end']}",
        "Billed cost: UNKNOWN / NOT_VERIFIED (calculated at published rates only).",
    ]
    (OUT / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(report, indent=2, default=jdefault))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
