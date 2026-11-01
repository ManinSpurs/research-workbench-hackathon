"""STANDARD live smoke runner for the approved plan current/LIVE_API_SMOKE_PLAN_EN.md.

Five sequential app-flow calls (Spark reasoning, transcription, image, SAM
segmentation, Wonder standalone second-look) via AIService.prepare -> explicit
consent -> AIService.execute. Synthetic-only payloads. No raw HTTP, no key
logging (only SET/UNSET is ever printed). Halts on the first anomaly.
"""
import asyncio
import base64
import io
import json
import math
import struct
import sys
import time
import wave
import zlib
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

OUT = ROOT.parent / "logs" / "LIVE_SMOKE_STANDARD_2026-10-26"
HARD_CAP = 0.20
TRIP = {
    "spark": 0.05,
    "transcribe": 0.01,
    "image": 0.03,
    "segment": 0.03,
    "wonder": 0.08,
}
EXPECTED_IDS = {
    "muse-spark-1.3",
    "muse-wonder-1.0",
    "muse-image-1.0",
    "muse-voice-transcribe-1.0",
    "sam-3.1",
}


def tone_wav_2s() -> str:
    rate = 16000
    n = rate * 2
    frames = b"".join(
        struct.pack("<h", int(12000 * math.sin(2 * math.pi * 440 * i / rate)))
        for i in range(n)
    )
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(frames)
    return base64.b64encode(buf.getvalue()).decode()


def circle_png_64() -> str:
    w = h = 64
    rows = []
    for y in range(h):
        row = bytearray([255, 255, 255])
        for x in range(1, w):
            dx, dy = x - 32, y - 32
            px = (0, 0, 0) if dx * dx + dy * dy <= 20 * 20 else (255, 255, 255)
            row += bytes(px)
        rows.append(b"\x00" + bytes(row))
    raw = (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + _chunk(b"IDAT", zlib.compress(b"".join(rows)))
        + _chunk(b"IEND", b"")
    )
    return base64.b64encode(raw).decode()


def _chunk(tag: bytes, data: bytes) -> bytes:
    import binascii

    return struct.pack(">I", len(data)) + tag + data + struct.pack(
        ">I", binascii.crc32(tag + data) & 0xFFFFFFFF
    )


def jdefault(o):
    return str(o)


async def main() -> int:
    OUT.mkdir(parents=True, exist_ok=True)
    h = Harness(str(OUT / "scratch_qa.db"), adapter=OfflineAdapter())
    svc = AIService(h)
    report = {
        "plan": "current/LIVE_API_SMOKE_PLAN_EN.md",
        "key": None,
        "catalog_ok": False,
        "settings_before": None,
        "settings_after": None,
        "rows": [],
        "running_total": 0.0,
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
        need = 5.520728 + report["settings_before"]["local_committed_or_reserved_usd"]
        if after_enable["local_allowance_remaining_usd"] < need:
            report["halt"] = (
                "allowance insufficient for ~$5.521 total reservation; stopped"
            )
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
        missing = EXPECTED_IDS - set(catalog["catalog"])
        if missing:
            report["halt"] = f"catalog missing ids {sorted(missing)}; stopped before any send"
            return finish(svc, h, report)
        report["catalog_ok"] = True

        pid = h.register_project("fx")["project_id"]

        rows = [
            {
                "key": "spark",
                "model": "muse-spark-1.3",
                "task": "research_query",
                "file": "job_1_spark.json",
                "qtext": "Synthetic check: what does est_cost measure in this scratch fixture?",
                "body": {
                    "task": "research_query",
                    "model": "muse-spark-1.3",
                    "text": "In one sentence, define plan-review completion. Use only the provided context.",
                    "harness": False,
                    "compare": False,
                    "max_output_tokens": 128,
                    "max_cost_usd": 5.0,
                },
            },
            {
                "key": "transcribe",
                "model": "muse-voice-transcribe-1.0",
                "task": "transcribe",
                "file": "job_2_transcribe.json",
                "qtext": "Synthetic check: transcribe the scratch tone fixture.",
                "asset": ("tone_2s.wav", "audio/wav", tone_wav_2s()),
                "body": {
                    "task": "transcribe",
                    "model": "muse-voice-transcribe-1.0",
                    "harness": False,
                    "compare": False,
                    "max_cost_usd": 5.0,
                },
            },
            {
                "key": "image",
                "model": "muse-image-1.0",
                "task": "image_generate",
                "file": "job_3_image.json",
                "qtext": "Synthetic check: generate the scratch icon fixture.",
                "body": {
                    "task": "image_generate",
                    "model": "muse-image-1.0",
                    "text": "A tiny flat red-square icon on white, minimal, 256px.",
                    "harness": False,
                    "compare": False,
                    "max_cost_usd": 5.0,
                },
            },
            {
                "key": "segment",
                "model": "sam-3.1",
                "task": "segment",
                "file": "job_4_segment.json",
                "qtext": "Synthetic check: segment the scratch circle fixture.",
                "asset": ("circle_64.png", "image/png", circle_png_64()),
                "body": {
                    "task": "segment",
                    "model": "sam-3.1",
                    "text": "List one bounding box for the circle as JSON.",
                    "harness": False,
                    "compare": False,
                    "max_cost_usd": 5.0,
                },
            },
            {
                "key": "wonder",
                "model": "muse-wonder-1.0",
                "task": "methodology_review",
                "file": "job_5_wonder.json",
                "qtext": "Synthetic check: bounded next check for this scratch fixture.",
                "body": {
                    "task": "methodology_review",
                    "model": "muse-wonder-1.0",
                    "text": "In one sentence, state the bounded next check. Use only the provided subset.",
                    "max_output_tokens": 128,
                    "max_cost_usd": 5.0,
                },
            },
        ]

        for i, row in enumerate(rows, 1):
            entry = {
                "n": i,
                "row": row["key"],
                "model": row["model"],
                "task": row["task"],
                "job_id": None,
                "status": None,
                "usage": None,
                "calculated_cost_usd": None,
                "elapsed_seconds": None,
                "running_total": report["running_total"],
            }
            try:
                q = await h.create_question(pid, row["qtext"])
                qid = q["question_id"]
                entry["question_id"] = qid
                body = dict(row["body"])
                if row.get("asset"):
                    name, mime, enc = row["asset"]
                    reg = svc.register_asset(
                        qid, {"name": name, "mime": mime, "encoded": enc}
                    )
                    body["assets"] = [reg["id"]]
                    entry["asset"] = {
                        k: reg[k]
                        for k in ("id", "name", "mime", "kind", "sha256", "bytes")
                        if k in reg
                    }
                    if "duration_seconds" in reg:
                        entry["asset"]["duration_seconds"] = reg["duration_seconds"]
                preview = svc.prepare(qid, body)
                entry["job_id"] = preview["job_id"]
                entry["preview_hash"] = preview["preview_hash"]
                entry["reservation"] = preview["maximum_reserved_usd"]
                if preview["model"] != row["model"]:
                    raise RuntimeError(
                        f"preview model {preview['model']} != plan {row['model']}"
                    )
                if preview.get("data_use_mode") != "Standard":
                    raise RuntimeError("non-Standard data_use_mode in preview")
                if not preview.get("model_access_verified"):
                    raise RuntimeError("model_access_verified False in preview")
                tx = preview.get("transmission", {})
                if tx.get("destination") != "https://api.meta.ai/v1":
                    raise RuntimeError("unexpected preview destination")
                if row["key"] == "wonder":
                    if tx.get("includes_canvas_notes_and_question_decisions") is not False:
                        raise RuntimeError(
                            "Wonder preview would transmit full snapshot; row 5 dropped"
                        )
                    if "text_and_context" in tx:
                        raise RuntimeError(
                            "Wonder preview carries full context; row 5 dropped"
                        )
                started = time.perf_counter()
                result = await svc.execute(
                    preview["job_id"], preview["preview_hash"], True
                )
                entry["elapsed_seconds"] = time.perf_counter() - started
                stages = result.get("stages", [])
                entry["usage"] = [s.get("usage") for s in stages]
                entry["calculated_cost_usd"] = result.get("calculated_cost_usd")
                entry["cost_basis"] = [s.get("cost_basis") for s in stages]
                entry["response_model"] = [s.get("model") for s in stages]
                usage_rows = [
                    dict(r)
                    for r in svc.db.execute(
                        "SELECT * FROM ai_usage WHERE job_id=?", (preview["job_id"],)
                    )
                ]
                for u in usage_rows:
                    u["accounting_json"] = json.loads(u["accounting_json"])
                spend_row = dict(
                    svc.db.execute(
                        "SELECT * FROM ai_spend WHERE job_id=?", (preview["job_id"],)
                    ).fetchone()
                )
                receipt = svc.receipt(preview["job_id"])
                (OUT / row["file"]).write_text(
                    json.dumps(
                        {
                            "row": row["key"],
                            "model": row["model"],
                            "task": row["task"],
                            "question_id": qid,
                            "preview": preview,
                            "result": result,
                            "usage_rows": usage_rows,
                            "spend_row": spend_row,
                        },
                        indent=2,
                        default=jdefault,
                    )
                )
                (OUT / f"receipt_{preview['job_id']}.json").write_text(
                    json.dumps(receipt, indent=2, default=jdefault)
                )
                entry["receipt_file"] = f"receipt_{preview['job_id']}.json"
                if (
                    not result.get("usage_complete")
                    or entry["calculated_cost_usd"] is None
                ):
                    raise RuntimeError("missing/unparseable usage (unknown charge)")
                if row["key"] in ("spark", "wonder"):
                    if len(stages) != 1:
                        raise RuntimeError("reasoning row must be single-call")
                if row["key"] == "image":
                    n_img = len((result.get("outputs") or {}).get("images", []))
                    if n_img != 1:
                        raise RuntimeError(
                            f"image contract deviation: {n_img} image results"
                        )
                cost = entry["calculated_cost_usd"]
                if cost > TRIP[row["key"]]:
                    raise RuntimeError(
                        f"cost {cost} above per-call trip line {TRIP[row['key']]}"
                    )
                report["running_total"] = round(report["running_total"] + cost, 8)
                entry["running_total"] = report["running_total"]
                if report["running_total"] > HARD_CAP:
                    raise RuntimeError("running total above $0.20 hard cap")
                entry["status"] = "completed"
                report["rows"].append(entry)
            except (Conflict, ProviderError, RuntimeError, ValueError) as exc:
                entry["status"] = entry.get("status") or "halted"
                entry["error"] = getattr(exc, "code", None) or str(exc)[:200]
                report["rows"].append(entry)
                if row["key"] == "wonder" and "row 5 dropped" in str(
                    getattr(exc, "code", None) or exc
                ):
                    report["halt"] = (
                        f"row 5 restricted-subset check failed after {i - 1} calls; "
                        "row 5 dropped and run stopped per plan"
                    )
                else:
                    report["halt"] = (
                        f"halted at row {i} ({row['key']}): "
                        f"{getattr(exc, 'code', None) or str(exc)[:200]}"
                    )
                return finish(svc, h, report)
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
    (OUT / "plan_reference.md").write_text(
        "# Plan reference\n\n"
        "- Plan: `current/LIVE_API_SMOKE_PLAN_EN.md` (STANDARD scope, 5 calls).\n"
        f"- Instruction-contract fingerprint: `{report['contract_fingerprint']}`\n"
        "  (sha256 of [SAFE_CORE, FRAME_CONTRACT, FINAL_CONTRACT, WIRE_VERSION]\n"
        "  in `current/rehearsal_build/app/ai/service.py`, re-verified at run time).\n"
        "- Provider billed cost: UNKNOWN / NOT_VERIFIED in every artifact by design.\n"
    )
    lines = [
        "# Live smoke summary (STANDARD, 2026-10-26)",
        "",
        f"Key gate: {report['key']}; catalog_ok: {report['catalog_ok']}",
        f"DB: `{OUT / 'scratch_qa.db'}` (isolated scratch, synthetic FX only)",
        "",
        "| # | row | model | job_id | status | usage | cost (calc) | elapsed s | running total |",
        "|---|-----|-------|--------|--------|-------|-------------|-----------|---------------|",
    ]
    for e in report["rows"]:
        lines.append(
            f"| {e.get('n')} | {e.get('row')} | {e.get('model')} | {e.get('job_id')} | "
            f"{e.get('status')} | {json.dumps(e.get('usage'), default=jdefault)} | "
            f"{e.get('calculated_cost_usd')} | {e.get('elapsed_seconds')} | "
            f"{e.get('running_total')} |"
        )
    lines += [
        "",
        f"Running total vs $0.20 cap: {report['running_total']}",
        f"Halt point: {report['halt'] or 'none — all 5 rows completed'}",
        f"Cloud off at end: {report['cloud_off_at_end']}",
        "Billed cost: UNKNOWN / NOT_VERIFIED (calculated at published rates only).",
    ]
    (OUT / "SUMMARY.md").write_text("\n".join(lines) + "\n")
    print(json.dumps(report, indent=2, default=jdefault))
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
