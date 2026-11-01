"""Isolated, content-approved comparison. No inference during preparation."""
import argparse
import asyncio
import contextlib
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import random
import shlex
import shutil
import socket
import sqlite3
import tempfile

from app.harness import Harness
from app.offline import OfflineAdapter
from app.packs import get_pack
from . import local_config, local_launcher, local_runtime, service
from .evaluation_runner import build_scoring_packet
from .provider import ProviderError
from .registry import BASETEN_MODEL_ID
from .receipt import verify_result_digest

ROOT = Path(__file__).resolve().parents[2]
LOGS = ROOT.parent / "logs"
FORBIDDEN_PORTS = {8123, 5173, 8301, 18123, 15173}
# A new reuse source requires a deliberate code pin, never a plan-supplied hash.
PINNED_REUSE_DB_SHA256 = frozenset({
    "8bfebc2cd01bf4039d680bbffea146de3b6e22496ccb6bbbb2affbd69f551076",
    "4eea389058819fa3fb3bfb65e68be0b3b98fb13c6c9544ae0727a3e4403c5606",
})


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n")


def source_hash():
    files = sorted((ROOT / "app/ai").glob("*.py"))
    files += [ROOT / "tools/rehearse_fresh_comparison.py", ROOT / "app/harness.py",
              ROOT / "app/packs.py"]
    return service.digest({str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                           for p in files})


@contextlib.contextmanager
def runtime(config, log):
    old = local_runtime.published_binding()
    server = local_launcher.LocalServer(config, log_path=log)
    try:
        server.start()
        local_runtime.publish_binding(local_launcher.binding_environment(server))
        yield
    finally:
        local_runtime.publish_binding(old)
        server.stop()


def content_record(record):
    """Exclude only launch/job nonces, never research content or destinations.

    App previews are refreshed after restart and still executed using their own
    exact, unmodified hashes. This durable approval additionally binds their
    substantive content, across the two verified runtime launches.
    """
    content = copy.deepcopy(record)
    content.pop("job_binding")
    if "local_binding" in content:
        for key in ("launch_run", "launch_server_pid", "run_fingerprint"):
            content["local_binding"].pop(key)
    return content


def stages(svc, record):
    common = service.SAFE_CORE + "\nDOMAIN: " + get_pack(record["snapshot"]["pack"]["pack_id"]).instructions
    calls = []
    for arm in record["execution_order"]:
        if arm == "harness":
            calls.append({"stage": "frame", "body": svc._body(
                record, [], common + "\n" + service.FRAME_CONTRACT, None, "frame")})
        body = svc._body(record, [], common + "\n" + service.FINAL_CONTRACT)
        calls.append({"stage": arm, "body": body,
                      **({"conditional_extra": {
                          "bounded_plan": "validated frame response (not yet generated)",
                          "observations": "all registered entries selected by that frame's source IDs",
                          "maximum_extra": service.worst_harness_extras(record["snapshot"]["packet"])
                      }} if arm == "harness" else {})})
    return calls


def preview_jobs(svc, questions, route, model=None):
    previews = []
    for question in questions:
        body = {"task": "research_query", "model": model or (
                    local_runtime.MODEL_ID if route == "local" else BASETEN_MODEL_ID),
                "provider": route, "mode": "local" if route == "local" else "cloud",
                "harness": True, "compare": True, "text": question["question"],
                "assets": [], "max_output_tokens": 2048, "max_cost_usd": 5.0}
        preview = svc.prepare(question["question_id"], body)
        record = json.loads(svc.db.execute("SELECT request_json FROM ai_jobs WHERE id=?",
                                          (preview["job_id"],)).fetchone()[0])
        if record["execution_order"] != question["order"]:
            raise ValueError("frozen_execution_order_changed")
        content = content_record(record)
        previews.append({"case_id": question["case_id"], "preview": preview,
                         "content": content, "content_hash": service.digest(content),
                         "calls": stages(svc, record)})
    return previews


class CostStop:
    def __init__(self, folder):
        self.total = 0.0
        self.folder = folder
        self.provider = "baseten"
        self.provider_totals = {}

    def __call__(self, stage):
        cost = stage["cost_usd"]
        if cost is None:
            raise ProviderError("comparison_usage_unknown_stop")
        self.total += cost
        self.provider_totals[self.provider] = self.provider_totals.get(self.provider, 0) + cost
        save(self.folder / "spend.json", {"cumulative_calculated_usd": self.total,
                                         "round_provider_calculated_usd": self.provider_totals,
                                         "last_call": stage})
        if cost > 1:
            raise ProviderError("comparison_single_call_over_1_usd")
        if self.total > 5:
            raise ProviderError("comparison_cumulative_over_5_usd")


def job_summary(route, case_id, result, receipt):
    outputs = result.get("outputs") or {}
    stages = result.get("stages") or []
    return {"provider": route, "case_id": case_id,
            "elapsed_seconds": result.get("elapsed_seconds", sum(
                stage.get("elapsed_seconds") or 0 for stage in stages)),
            "elapsed_basis": "job_wall_time" if "elapsed_seconds" in result else "sum_of_recorded_provider_stages",
            "stages": stages, "calculated_cost_usd": result.get("calculated_cost_usd"),
            "measured_stage_cost_usd": sum(s.get("cost_usd") or 0 for s in stages),
            "job_status": receipt["job_status"], "error_code": receipt.get("error_code"),
            "validation": {arm: outputs[arm]["grounding"] for arm in ("basic", "harness")
                           if arm in outputs}}


def finish(folder, plan, results, ids, summaries, total):
    available = [(index, result, ids[index]) for index, result in enumerate(results)
                 if isinstance(result.get("outputs"), dict) and
                 any(arm != "evaluation" for arm in result["outputs"])]
    scoring = build_scoring_packet([result for _, result, _ in available],
                                   [case_id for _, _, case_id in available])
    unblinding = scoring.pop("unblinding_key")
    unblinding["unavailable_jobs"] = [
        {"job_index": index, "case_id": ids[index], "error_code": result.get("error_code")}
        for index, result in enumerate(results)
        if not any(index == original for original, _, _ in available)]
    for case in scoring["cases"]:
        key = unblinding["cases"][case["case_id"]]
        codes = plan["blind_codes"][case["case_id"]]
        if len(codes) < len(key["mapping"]) or len(set(codes)) != len(codes):
            raise ValueError("comparison_blind_codes_invalid")
        rename = dict(zip(key["mapping"], codes))
        key["mapping"] = {rename[code]: value for code, value in key["mapping"].items()}
        for arm in case["blinded_arms"]:
            code = rename[arm["blind_code"]]
            entry = key["mapping"][code]
            entry["job_index"] = available[entry["job_index"]][0]
            key["mapping"][code]["run_params"] = arm.pop("run_params")
            arm["blind_code"] = code
        case["blinded_arms"].sort(key=lambda arm: arm["blind_code"])
    save(folder / "blind-scoring-packet.json", scoring)
    save(folder / "unblinding-key.json", unblinding)
    summary = {"jobs": summaries, "cumulative_calculated_usd": total,
               "all_jobs_completed": len(summaries) == (
                   sum(len(route["questions"]) for route in plan["routes"].values())
                   + len(plan.get("reused_jobs", []))) and all(
                   s["error_code"] is None for s in summaries),
               "packet_path": str(folder / "blind-scoring-packet.json")}
    save(folder / "execution-summary.json", summary)
    return summary


def verify_reused(prior, case_id, result, receipt):
    """Bind output and the complete receipt to the original read-only DB."""
    try:
        plan = json.loads((prior / "plan.json").read_text())["plan"]
        question = next(q for q in plan["routes"]["local"]["questions"] if q["case_id"] == case_id)
        # Run 1 predates continuation-in-one-command; both are execution stores,
        # never templates. Exactly one must contain the completed question.
        matches = []
        for name in ("local-execution.db", "local-continuation.db"):
            dbpath = prior / name
            if not dbpath.is_file():
                continue
            # Hash and query one in-memory snapshot, so the bytes that pass the
            # pin are exactly the bytes queried; reopening the path would let a
            # swap between the two reads substitute another database.
            snapshot = dbpath.read_bytes()
            if hashlib.sha256(snapshot).hexdigest() not in PINNED_REUSE_DB_SHA256:
                raise ValueError("source_db_unpinned")
            # WAL-mode files mark read/write versions 2 at header bytes 18-19;
            # an in-memory image cannot use WAL, so present it as rollback mode.
            image = bytearray(snapshot)
            image[18:20] = b"\x01\x01"
            with contextlib.closing(sqlite3.connect(":memory:")) as db:
                db.deserialize(bytes(image))
                db.row_factory = sqlite3.Row
                for row in db.execute(
                    """SELECT j.id AS job_id,j.result_json,j.result_digest,j.status,
                       s.reservation,s.actual_cost,s.status AS spend_status
                       FROM ai_jobs j JOIN ai_spend s ON s.job_id=j.id
                       WHERE j.question_id=? AND j.status='completed_known'""",
                    (question["question_id"],)):
                    matches.append({**dict(row), "receipt": service.AIService.receipt_from_db(db,row["job_id"])})
        if len(matches) != 1:
            raise ValueError("missing_or_ambiguous_execution")
        row = matches[0]
        stored = json.loads(row["result_json"])
        if verify_result_digest(row["result_digest"], stored, row["reservation"],
                                row["actual_cost"], row["spend_status"]) is not True:
            raise ValueError("invalid_execution_digest")
        if service.digest(stored) != service.digest(result):
            raise ValueError("output_mismatch")
        if receipt.get("result_integrity_verified") is not True or receipt.get("job_status") != row["status"]:
            raise ValueError("receipt_integrity_missing")
        if service.digest(receipt) != service.digest(row["receipt"]):
            raise ValueError("receipt_mismatch")
    except (OSError, KeyError, StopIteration, sqlite3.Error, ValueError, TypeError) as exc:
        raise ValueError("reuse_provenance_invalid: " + str(exc)) from None


def prepare(cases, reuse_local_from=None, route_models=None):
    route_models = route_models or {
        "baseten": BASETEN_MODEL_ID, "local": local_runtime.MODEL_ID}
    if reuse_local_from:
        route_models = {route: model for route, model in route_models.items()
                        if route != "local"}
    LOGS.mkdir(parents=True, exist_ok=True)
    folder = Path(tempfile.mkdtemp(prefix=datetime.now(timezone.utc).strftime(
        "comparison_%Y%m%dT%H%M%SZ_"), dir=LOGS))
    workspace = Path(tempfile.mkdtemp(prefix="rw-comparison-", dir=LOGS))
    data = workspace / "data"
    for name in ("fx", "nyc"):
        shutil.copytree(ROOT / "data" / name, data / name)
    config = local_config.read()
    if not config and "local" in route_models:
        raise ValueError("local_runtime_unconfigured")
    # A temporary launch profile, never the user's saved configuration.
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    if port in FORBIDDEN_PORTS:
        raise ValueError("comparison_port_forbidden")
    config = {**(config or {}), "port": port, "base_url": "http://127.0.0.1:%d" % port,
              "context": 65536}
    plan = {"version": 1, "source_hash": source_hash(), "workspace": str(workspace),
            "runtime": config, "routes": {}, "repetitions": 1,
            "scope": {"paid_calls": 9, "local_calls": 9},
            "final_prompt_note": "Harness final contains the future validated frame and its selected registered observations; exact future bytes cannot exist before inference.",
            "retry_policy": "No automatic retries or replays",
            "spend_limits": {"approved_total_usd": 40, "stop_single_call_over_usd": 1,
                             "stop_cumulative_over_usd": 5}}
    arm_count = 2 * (len(route_models) + (1 if reuse_local_from else 0))
    codes = ["B%03d" % index for index in range(arm_count)]
    plan["blind_codes"] = {case.case_id: random.SystemRandom().sample(codes, arm_count)
                          for case in cases}
    if reuse_local_from:
        prior = Path(reuse_local_from).resolve()
        if not prior.is_relative_to(LOGS.resolve()):
            raise ValueError("reuse_outside_logs")
        prior_summary = json.loads((prior / "execution-summary.json").read_text())
        plan["previous_calculated_usd"] = prior_summary["cumulative_calculated_usd"]
        plan["reused_jobs"] = []
        for case in cases:
            result = json.loads((prior / ("local-" + case.case_id + "-output.json")).read_text())
            receipt = json.loads((prior / ("local-" + case.case_id + "-receipt.json")).read_text())
            verify_reused(prior, case.case_id, result, receipt)
            if receipt["job_status"] != "completed_known":
                raise ValueError("local_reuse_not_completed")
            plan["reused_jobs"].append({"case_id": case.case_id, "result": result,
                "receipt": receipt, "result_hash": service.digest(result), "source": str(prior)})
        plan["scope"]["local_calls"] = 0
    with (runtime(config, folder / "prepare-runtime.log") if "local" in route_models
          else contextlib.nullcontext()):
        for route, model in route_models.items():
            h = Harness(str(workspace / (route + ".db")), data, adapter=OfflineAdapter())
            try:
                svc = service.AIService(h)
                svc.configure(True, 40)
                if route != "local":
                    asyncio.run(svc.verify_account(route))
                questions = []
                for index, case in enumerate(cases):
                    pid = h.register_project(case.project_kind)["project_id"]
                    qid = asyncio.run(h.create_question(pid, case.question, pack_id=(
                        "engineering_measurement" if index == 0 else "social_science")))["question_id"]
                    questions.append({"question_id": qid, "question": case.question,
                                      "case_id": case.case_id, "order": list(case.expected_order)})
                with sqlite3.connect(workspace / (route + "-template.db")) as template:
                    h.db.backup(template)
                plan["routes"][route] = {"questions": questions, "model": model,
                                         "jobs": preview_jobs(svc, questions, route, model)}
            finally:
                h.db.close()
    jobs = [j for route in plan["routes"].values() for j in route["jobs"]]
    plan["scope"] = {
        "paid_calls": sum(len(j["calls"]) for route, spec in plan["routes"].items()
                          if route != "local" for j in spec["jobs"]),
        "local_calls": sum(len(j["calls"]) for route, spec in plan["routes"].items()
                           if route == "local" for j in spec["jobs"])}
    plan["maximum_reserved_usd"] = round(sum(j["preview"]["maximum_reserved_usd"] for j in jobs), 8)
    plan["reference_spend_scenario_usd"] = {"value": 0.037026,
        "basis": "9 times the user's prior $0.004114 claim check; different prompts, not a prediction"}
    plan_hash = service.digest(plan)
    save(folder / "plan.json", {"plan_hash": plan_hash, "plan": plan})
    hashes = [j["content_hash"] for j in jobs]
    command = ("TMPDIR=/tmp .venv/bin/python tools/rehearse_fresh_comparison.py execute --plan "
               + shlex.quote(str(folder / "plan.json")) + " --plan-hash " + plan_hash
               + " --content-hashes " + ",".join(hashes))
    summary = {"plan_path": str(folder / "plan.json"), "plan_hash": plan_hash,
               "content_hashes": hashes, "paid_calls": plan["scope"]["paid_calls"], "local_calls": plan["scope"]["local_calls"],
               "maximum_reserved_usd": plan["maximum_reserved_usd"],
               "reference_spend_scenario_usd": plan["reference_spend_scenario_usd"],
               "inference_calls": 0, "execute_command": command}
    save(folder / "preview-summary.json", summary)
    return summary


def execute(path, expected_plan, expected_hashes):
    path = Path(path).resolve()
    if not path.is_relative_to(LOGS.resolve()):
        raise ValueError("comparison_plan_outside_logs")
    packet = json.loads(path.read_text())
    plan = packet["plan"]
    if service.digest(plan) != expected_plan or packet["plan_hash"] != expected_plan:
        raise ValueError("comparison_plan_hash_mismatch")
    if plan["source_hash"] != source_hash():
        raise ValueError("comparison_source_changed")
    prepared_jobs = [j for r in plan["routes"].values() for j in r["jobs"]]
    if expected_hashes != [j["content_hash"] for j in prepared_jobs]:
        raise ValueError("comparison_content_hashes_mismatch")
    for job in prepared_jobs:
        if service.digest(job["content"]) != job["content_hash"]:
            raise ValueError("comparison_content_corrupt")
    for reused in plan.get("reused_jobs", []):
        verify_reused(Path(reused["source"]), reused["case_id"], reused["result"], reused["receipt"])
    folder = path.parent
    # Exclusive creation prevents a second paid run, even after partial failure.
    with (folder / "execute-started").open("x") as marker:
        marker.write(datetime.now(timezone.utc).isoformat())
    workspace = Path(plan["workspace"])
    stop = CostStop(folder)
    stop.total = plan.get("previous_calculated_usd", 0.0)
    results, ids, summaries = [], [], []
    with (runtime(plan["runtime"], folder / "execute-runtime.log")
          if "local" in plan["routes"] else contextlib.nullcontext()), contextlib.ExitStack() as cleanup:
        ready = []
        for route, spec in plan["routes"].items():
            db = folder / (route + "-execution.db")
            shutil.copyfile(workspace / (route + "-template.db"), db)
            h = Harness(str(db), workspace / "data", adapter=OfflineAdapter())
            cleanup.callback(h.db.close)
            svc = service.AIService(h)
            if route != "local":
                asyncio.run(svc.verify_account(route))
                svc.after_call = stop
            fresh = preview_jobs(svc, spec["questions"], route, spec.get("model"))
            if [j["content_hash"] for j in fresh] != [j["content_hash"] for j in spec["jobs"]]:
                raise ValueError("comparison_live_preview_content_changed")
            ready.append((route, spec, svc, fresh))
        for route, spec, svc, fresh in ready:
            provider_blocked = False
            for job in fresh:
                    stop.provider = route
                    preview = job["preview"]
                    stem = route + "-" + job["case_id"]
                    save(folder / (stem + "-preview.json"), job)
                    if provider_blocked:
                        reason = "provider_prior_unknown_charge"
                        result = {"error_code": reason}
                        save(folder / (stem + "-block.json"), result)
                        save(folder / (stem + "-receipt.json"), svc.receipt(preview["job_id"]))
                        results.append(result)
                        ids.append(job["case_id"])
                        summaries.append({"provider": route, "case_id": job["case_id"],
                                          "job_status": "blocked", "error_code": reason})
                        continue
                    failure = None
                    try:
                        if route != "local" and (
                                stop.total + preview["maximum_reserved_usd"] > 5):
                            raise ProviderError("comparison_reservation_would_cross_5_usd")
                        result = asyncio.run(svc.execute(preview["job_id"], preview["preview_hash"], True))
                    except BaseException as exc:
                        failure = exc
                        save(folder / (stem + "-receipt.json"), svc.receipt(preview["job_id"]))
                        recorded = svc.jobs(next(q["question_id"] for q in spec["questions"]
                                                 if q["case_id"] == job["case_id"]))
                        save(folder / (stem + "-recorded-jobs.json"), recorded)
                        result = next(j["result"] for j in recorded if j["id"] == preview["job_id"]) or {}
                    save(folder / (stem + "-output.json"), result)
                    receipt = svc.receipt(preview["job_id"])
                    provider_blocked = route != "local" and receipt["job_status"] in (
                        "failed_unknown_charge", "completed_unknown_charge")
                    save(folder / (stem + "-receipt.json"), receipt)
                    results.append(result)
                    ids.append(job["case_id"])
                    summaries.append(job_summary(route, job["case_id"], result, receipt))
                    if failure is not None and (not isinstance(failure, Exception) or
                            receipt.get("error_code") not in ("response_not_completed", "typed_output_validation_failed")):
                        finish(folder, plan, results, ids, summaries, stop.total)
                        raise failure
    for reused in plan.get("reused_jobs", []):
        if service.digest(reused["result"]) != reused["result_hash"]:
            raise ValueError("reused_result_hash_mismatch")
        results.append(reused["result"])
        ids.append(reused["case_id"])
        summaries.append(job_summary("local_reused", reused["case_id"], reused["result"], reused["receipt"]))
        save(folder / ("local-" + reused["case_id"] + "-output.json"), reused["result"])
        save(folder / ("local-" + reused["case_id"] + "-receipt.json"), reused["receipt"])
    return finish(folder, plan, results, ids, summaries, stop.total)


def main(argv, cases):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "execute"))
    parser.add_argument("--plan")
    parser.add_argument("--plan-hash")
    parser.add_argument("--content-hashes")
    parser.add_argument("--reuse-local-from")
    parser.add_argument("--route-model", action="append", metavar="PROVIDER=MODEL",
                        help="Repeat for each provider; defaults to Baseten and local")
    parser.add_argument("--case-set", choices=("original", "round3"), default="original")
    args = parser.parse_args(argv)
    if args.case_set == "round3":
        from tools.round3_cases import ROUND3_CASES
        cases = ROUND3_CASES
    if args.action == "prepare":
        routes = dict(value.split("=", 1) for value in args.route_model) if args.route_model else None
        result = prepare(cases, args.reuse_local_from, routes)
    else:
        if not all((args.plan, args.plan_hash, args.content_hashes)):
            parser.error("execute requires --plan, --plan-hash and --content-hashes")
        result = execute(args.plan, args.plan_hash, args.content_hashes.split(","))
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0
