"""Review-build backend supervisor: local model, mock provider, API, frontend.

This is the only process a packaged review build starts from Python. It owns
three loopback listeners (fake OpenAI-compatible provider, FastAPI backend,
static frontend), one SQLite database under the persistent state dir it is given
(kept across launches, like an installed app), and optionally one app-owned
local model server. Nothing here reads,
writes or requires the source working tree: every path is derived from this
file's own location inside the .app.

Cloud routes start switched off. The legacy Meta adapter uses the scripted fake
provider; Baseten, OpenAI and Modal Shared are real endpoints that run only after
the user saves their own key in Settings, switches cloud on and consents to the
calculated cost. The sample projects are seeded on the first launch only. It
never opens the project's local_rehearsal.db.

Local model: optional, never bundled
-----------------------------------
The weights and the ``llama-server`` executable are *not* in this bundle. If the
person opening the app has configured a local runtime on this Mac (the same
owner-only private file the source launcher uses), this supervisor starts it as
its own child before the API serves requests, verifies the server's own identity,
measures the chat template overhead and publishes the verified binding into this
process's environment only. With no configuration the app starts exactly as
before and reports local mode as unavailable. A broken configuration or a failed
launch is reported as unavailable, never a crash and never a cloud fallback.

That private file is read from the *real* user's home, resolved once before this
process moves ``HOME`` into the session (see :func:`_resolve_local_config_path`).
The workspace's private ``HOME`` and credential isolation are unchanged: a key
typed into this build's Settings lands inside the persistent workspace.

The model child is started in its own session, and a small watchdog
(:mod:`app.ai.local_reaper`) is armed for it, so a crash or ``SIGKILL`` of this
supervisor cannot leave the model running indefinitely.
"""
import argparse
import errno
import fcntl
import importlib
import json
import os
import shutil
import signal
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# Layout inside the bundle: <app>/packaging/backend_server.py
APP_ROOT = Path(__file__).resolve().parent.parent
SITE_PACKAGES = APP_ROOT / "runtime" / "site-packages"
FIXTURE_DATA = APP_ROOT / "data"
#: The name the "fx" fixture kind registers under (app/harness.py PROJECT_KINDS).
#: Read-before-write for the synthetic sample keys on it, so a session that
#: already holds the sample is recognised instead of being seeded again.
FX_SAMPLE_NAME = "FX-PERMIT-1 synthetic study"
FRONTEND = APP_ROOT / "frontend"

#: Where this app's private local-runtime configuration lives for the real user.
REAL_HOME_CONFIG = Path("Library/Application Support/ResearchWorkbench/local_runtime.json")

#: Every environment name that carries a local *run* binding or its credential.
#: These describe one verified launch, so they must never be inherited from
#: whatever started this process: a stale WB_LOCAL_API_KEY and origin in the
#: launching shell would otherwise make the API believe a runtime exists that
#: this process never started. Stripped first; a verified launch then publishes
#: the binding in-process through ``local_runtime.publish_binding``, so the
#: bearer itself is never exported to this process's environment (and therefore
#: never inherited by a child such as ``pdftotext`` or ``xcrun``).
LOCAL_RUN_ENV = ("WB_LOCAL_BASE_URL", "WB_LOCAL_MODEL", "WB_LOCAL_CONTEXT",
                 "WB_LOCAL_RUN_ID", "WB_LOCAL_SERVER_PID", "WB_LOCAL_WEIGHTS_SHA256",
                 "WB_LOCAL_TEMPLATE_OVERHEAD_TOKENS", "WB_LOCAL_OBSERVED_ALIAS",
                 "WB_LOCAL_OBSERVED_CTX", "WB_LOCAL_OBSERVED_CTX_TRAIN",
                 "WB_LOCAL_API_KEY")

#: Every cloud credential variable ``app/ai/secrets.py`` reads from the
#: environment (its ``PROVIDER_SECRETS`` override names). A review session must
#: inherit no machine credential: the build's claim is that a key only ever
#: exists if the person types one into this session's Settings. Each of these is
#: an *override* for the owner-only key file, which does not exist under the
#: session HOME either, so stripping them cannot break a documented packaged
#: feature — the Modal Shared route still works with a key typed in Settings,
#: which is written to this session's own key file.
CLOUD_KEY_ENV = ("META_API_KEY", "MODEL_API_KEY", "BASETEN_API_KEY",
                 "OPENAI_API_KEY", "WB_MODAL_SHARED_API_KEY")

#: Whole budget for starting the local model, in seconds. The desktop shell gives
#: this backend 90 s to report ready; verifying the pinned weights takes about a
#: second and loading the model a few seconds more, so this ceiling leaves a wide
#: margin while still failing rather than hanging if the model cannot load.
#:
#: Nothing inside the start may add to this: one absolute deadline covers the
#: digest, every readiness probe and the template measurement, and the
#: failure-path stop uses the launcher's shorter grace. The worst case is
#: therefore about 1 s (port probe) + 50 s (start) + 3 s + 2 s (failure stop),
#: which leaves over 30 s of the shell's timeout for the API and the ready file.
LOCAL_STARTUP_TIMEOUT = 50.0


def _free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def _resolve_local_config_path(environ=None):
    """The real user's local-runtime config path, resolved before HOME moves.

    ``app.ai.local_config.config_path()`` prefers ``WB_LOCAL_CONFIG`` and
    otherwise uses ``Path.home()``. ``Path.home()`` is the real user's home only
    until this process overrides ``HOME`` for the session, and the override
    happens before any application import, so the path is resolved here, once,
    and then pinned through ``WB_LOCAL_CONFIG``: every later read in this process
    resolves the same real file whatever ``HOME`` becomes.

    The pin is a path, never a credential, and it does not grant anything: the
    launcher still validates the file's ownership and mode and still verifies the
    weights' pinned size and digest before starting anything.
    """
    environ = os.environ if environ is None else environ
    override = environ.get("WB_LOCAL_CONFIG")
    if override:
        pinned = Path(override).expanduser()
    else:
        pinned = Path.home() / REAL_HOME_CONFIG
    environ["WB_LOCAL_CONFIG"] = str(pinned)
    return pinned


def _local_status(status, reason=None, *, binding=None, log=None):
    """A non-secret description of local mode for the console and the session.

    Only whitelisted, already-published values are copied. The per-run bearer and
    the key file path are deliberately absent: this dict is written to
    ``REVIEW_SESSION.json``, the ready file the shell reads and the console, and
    none of those may carry the model's credential.
    """
    available = status == "local_model_running" and binding is not None
    return {
        "available": available,
        "status": status,
        "reason": reason,
        "model": (binding or {}).get("model"),
        "context": (binding or {}).get("context"),
        "base_url": (binding or {}).get("base_url"),
        "template_overhead_tokens": (binding or {}).get("template_overhead_tokens"),
        "weights_sha256": (binding or {}).get("weights_sha256"),
        # Facts about what this build does and does not ship.
        "model_bundled": False,
        "cloud": False,
        "data_use": "local compute only; no cloud provider and no per-request billing",
        "log": log,
    }


def _start_local_runtime(state, app_origin):
    """Start and verify this app's own local model, or say honestly why not.

    Returns ``(server, status)``. An absent configuration, an invalid one and a
    failed launch are all *supported* states of a review build: each returns
    ``(None, status)`` with a short non-secret reason, so the app still starts and
    the console and session record why local mode is unavailable. Nothing here
    falls back to a cloud route, and nothing here raises for those cases.

    ``BaseException`` deliberately propagates: an interrupt during model load is
    handled by the caller's cleanup, which stops the owned child.
    """
    log_path = state / "local_runtime.log"
    try:
        from app.ai import local_config, local_launcher
    except Exception as exc:
        # A bundle without the local launcher still runs: local mode is simply
        # unavailable, and the console says so instead of the app failing.
        return None, _local_status("local_model_unavailable",
                                   "The local runtime launcher is not present in this build: %s"
                                   % exc, log=str(log_path))

    try:
        config = local_config.read()
    except ValueError as exc:
        return None, _local_status("local_model_config_invalid", str(exc), log=str(log_path))
    except Exception as exc:
        # An unreadable path (permissions, a broken volume) is not a valid
        # configuration either, and must not stop the app.
        return None, _local_status(
            "local_model_config_invalid",
            "The local runtime configuration could not be read: %s" % exc,
            log=str(log_path))
    if not config:
        return None, _local_status(
            "local_model_not_configured",
            "No local model is configured for this Mac, so local mode is unavailable; "
            "the bundled scripted mock stays the adapter for cloud routes.",
            log=str(log_path))
    server = local_launcher.LocalServer(config, log_path=str(log_path),
                                        app_origin=app_origin,
                                        startup_timeout=LOCAL_STARTUP_TIMEOUT,
                                        watch_parent=True, allow_port_fallback=True)
    try:
        server.start()
        # Publish what the launch *verified* into this process's own binding
        # store: origin, alias, context, run id, launched pid, weights digest,
        # measured template overhead, observed identity and the per-run bearer.
        # Deliberately NOT os.environ: this API runs in this process, so an
        # exported bearer would be inherited by every child spawned later
        # (pdftotext, xcrun/swiftc, any helper). The store keeps it inside this
        # process's objects, which is the same boundary a child environment
        # would give a separate API process.
        from app.ai import local_runtime
        local_runtime.publish_binding(local_launcher.binding_environment(server))
        # The published binding is what the API will use, so it is checked the
        # same way the API checks it: usable only if every field, including the
        # live pid and the measured overhead, is present and consistent. A
        # mismatch means local mode is not usable here, which is said rather
        # than assumed.
        usable = local_runtime.read_config()["configured"]
    except Exception as exc:
        # A failed launch is reported, never raised: the app must still start.
        # The stop uses the launcher's short failure-path grace so a model that
        # ignores SIGTERM cannot delay the ready file past the shell's timeout.
        _clear_published_binding()
        local_launcher.stop_preserving(server, grace=local_launcher.FAILURE_STOP_GRACE_SECONDS,
                                       kill_wait=local_launcher.FAILURE_KILL_WAIT_SECONDS)
        return None, _local_status("local_model_launch_failed", str(exc), log=str(log_path))
    except BaseException:
        # An interrupt (SIGTERM/SIGINT) mid-start is not a "local unavailable"
        # state: it ends the run, and the child that already exists must be
        # stopped before it propagates.
        _clear_published_binding()
        local_launcher.stop_preserving(server, grace=local_launcher.FAILURE_STOP_GRACE_SECONDS,
                                       kill_wait=local_launcher.FAILURE_KILL_WAIT_SECONDS)
        raise
    if not usable:
        _clear_published_binding()
        local_launcher.stop_preserving(server, grace=local_launcher.FAILURE_STOP_GRACE_SECONDS,
                                       kill_wait=local_launcher.FAILURE_KILL_WAIT_SECONDS)
        return None, _local_status(
            "local_model_binding_unverified",
            "The local model started but its verified binding is incomplete, so local "
            "mode stays unavailable for this session.",
            log=str(log_path))
    status = _local_status(
        "local_model_running",
        binding={"model": config["alias"], "context": config["context"],
                 "base_url": server.config["base_url"],
                 "template_overhead_tokens": server.template_overhead_tokens,
                 "weights_sha256": server.weights_sha256},
        log=str(log_path))
    return server, status


def _clear_published_binding():
    """Withdraw this process's local binding once the run it describes is over.

    A published binding says "a verified model is serving right now". After a
    failed launch or a stop that is no longer true, so the store is cleared
    rather than left to describe a runtime that is gone.
    """
    try:
        from app.ai import local_runtime
    except Exception:  # noqa: BLE001 - clearing must never be the reason startup fails
        return
    local_runtime.publish_binding(None)


def _stop_local_runtime(server, *, failure=False):
    """Stop the owned model on every exit path, reporting without raising.

    ``failure`` selects the launcher's shorter grace. It is used when the app is
    still starting and must reach its ready file inside the shell's timeout; a
    model that ignores both signals is then left to the watchdog, which stays
    armed, rather than holding the app's startup open.
    """
    from app.ai import local_launcher
    if failure:
        message = local_launcher.stop_preserving(
            server, grace=local_launcher.FAILURE_STOP_GRACE_SECONDS,
            kill_wait=local_launcher.FAILURE_KILL_WAIT_SECONDS)
    else:
        message = local_launcher.stop_preserving(server)
    _clear_published_binding()
    if message:
        print("[backend] %s" % message, file=sys.stderr, flush=True)


class _QuietStatic(SimpleHTTPRequestHandler):
    """Static frontend server. Loopback only; no directory listing above the root."""

    def log_message(self, fmt, *args):  # keep the console readable for the supervisor
        pass


def _start_static(port, directory):
    handler = lambda *a, **kw: _QuietStatic(*a, directory=str(directory), **kw)  # noqa: E731
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    thread = threading.Thread(target=server.serve_forever, name="wb-static", daemon=True)
    thread.start()
    return server


def _wait_started(server, label):
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        if server.started:
            return
        if getattr(server, "should_exit", False):
            raise RuntimeError(f"{label} failed to start")
        time.sleep(0.05)
    raise RuntimeError(f"{label} did not start within 60 seconds")


def _json(url, payload=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, method="POST" if data else "GET",
                                     headers={"Content-Type": "application/json"})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


def _seed(api_base):
    """Fresh sample workspace: the synthetic fixture plus the app's own project.

    Two projects, both public-safe, both read through routes the app already
    serves. First the synthetic FX-PERMIT-1 fixture, unchanged. Then "Research
    Workbench - hackathon build": the app's development as a real research
    project, seeded from ``data/dev_project/plan.json`` (folders, literature
    notes, recorded decisions, a LaTeX draft and a results table). Its content is
    authored in the source tree, derived from this repository's own design
    documents, and carries no credential, no absolute local path and no private
    data.

    Each project is seeded on its own, read-before-write: the FX sample is looked
    up by its own name and created only when absent, then the development project
    the same way. A session that holds only the FX sample therefore still gets the
    development project, and a seed interrupted part way through the development
    project is completed rather than restarted. Within each project every step is
    also read-before-write, so nothing is created twice.

    The library table is written through the route the engine work adds,
    ``POST /projects/{pid}/library/tables``; a build whose app does not serve that
    route yet reports the step as skipped instead of faking a table.
    """
    def call(path, payload=None):
        return _json(api_base + path, payload)

    def saved(report, project_id):
        if project_id:
            report["projects"].append(project_id)
        return report

    def seed_fx_sample(report):
        """The synthetic fixture, by name, created only when this session lacks it."""
        try:
            current = call("/projects")["projects"]
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            report["error"] = f"projects: {type(exc).__name__}: {exc}"
            return
        name = FX_SAMPLE_NAME
        found = next((p for p in current if p["name"] == name), None)
        if found is not None:
            saved(report, found["id"])
            return
        try:
            created = call("/projects", {"kind": "fx"})
            saved(report, created.get("id") or created.get("project_id"))
            report["created"]["project"] += 1
        except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
            # The synthetic sample is the one thing a review build must not lose, so
            # a failure here is reported and the development project still runs.
            report["error"] = f"fx sample: {type(exc).__name__}: {exc}"

    def seed_dev_project(api_base, report):
        """Apply plan.json over the existing routes, creating only what is missing."""
        plan_dir = FIXTURE_DATA / "dev_project"
        plan = json.loads((plan_dir / "plan.json").read_text(encoding="utf-8"))
        csv_text = (plan_dir / plan["project"]["csv_file"]).read_text(encoding="utf-8")
        name = plan["project"]["name"]
        existing = [p for p in call("/projects")["projects"] if p["name"] == name]
        if existing:
            pid = existing[0]["id"]
        else:
            created = call("/projects/upload", {"name": name, "csv_text": csv_text,
                                                "dictionary": plan["project"]["dictionary"]})
            pid = created.get("project_id") or created.get("id")
            report["created"]["project"] += 1
        saved(report, pid)

        tree = call("/library/tree?project_id=" + urllib.parse.quote(pid))
        folders = {folder["name"]: folder["id"] for folder in tree["folders"]}
        for folder_name in plan["folders"]:
            if folder_name not in folders:
                folders[folder_name] = call("/library/folders",
                                            {"project_id": pid, "name": folder_name})["id"]
                report["created"]["folders"] += 1

        notes = {(item["folder_id"], item["title"]) for item in tree["items"] if item["kind"] == "note"}
        for note in plan["notes"]:
            folder_id = folders[note["folder"]]
            if (folder_id, note["title"]) in notes:
                continue
            call("/library/notes", {"project_id": pid, "folder_id": folder_id,
                                    "title": note["title"],
                                    "text": (plan_dir / note["file"]).read_text(encoding="utf-8")})
            report["created"]["notes"] += 1

        events = call("/projects/" + urllib.parse.quote(pid) + "/events")["events"]
        asked, decided = {}, set()
        for event in events:
            payload = event.get("payload") or {}
            if isinstance(payload, str):
                payload = json.loads(payload)
            qid = event.get("question_id")
            if event["kind"] == "question_captured" and qid and payload.get("original_text"):
                asked[payload["original_text"]] = qid
            elif event["kind"] == "decision_committed" and qid:
                decided.add(qid)
        for decision in plan["decisions"]:
            qid = asked.get(decision["question"])
            if qid is None:
                qid = call("/projects/" + urllib.parse.quote(pid) + "/questions",
                           {"text": decision["question"]})["question_id"]
                report["created"]["questions"] += 1
            if qid not in decided:
                call("/projects/" + urllib.parse.quote(pid) + "/decisions",
                     {"question_id": qid, "verdict": decision["verdict"],
                      "reason": decision["reason"]})
                report["created"]["decisions"] += 1

        draft = plan["draft"]
        titles = {row["title"] for row in call("/projects/" + urllib.parse.quote(pid) + "/drafts")["drafts"]}
        if draft["title"] not in titles:
            call("/projects/" + urllib.parse.quote(pid) + "/drafts",
                 {"title": draft["title"],
                  "import": {"name": Path(draft["file"]).name,
                             "text": (plan_dir / draft["file"]).read_text(encoding="utf-8")}})
            report["created"]["drafts"] += 1

        table_title = plan["table"]["title"]
        has_table = any(item["kind"] == "table" and item["title"] == table_title
                        for item in tree["items"])
        if has_table:
            report["table"] = "table already present"
        else:
            # The one step another worker owns: exactly this route, called when the
            # app serves it. A 404 or 405 means the route has not landed in this
            # build, which is reported; no other path is tried and no table row is
            # written here.
            path = "/projects/" + urllib.parse.quote(pid) + "/library/tables"
            csv_text = (plan_dir / plan["project"]["csv_file"]).read_text(encoding="utf-8")
            try:
                call(path, {"title": table_title, "csv_text": csv_text})
            except urllib.error.HTTPError as exc:
                if exc.code not in (404, 405):
                    raise
                report["table"] = "table skipped: create_table not available yet"
            else:
                report["created"]["table"] += 1
                report["table"] = "table created"
        return report

    try:
        existing = call("/projects")["projects"]
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        return {"seeded": False, "error": f"{type(exc).__name__}: {exc}"}

    # Each project is independent and read-before-write, so both a session with
    # only the FX sample and a session interrupted inside the development project
    # are completed on the next launch instead of being skipped.
    report = {"seeded": True, "projects": [], "table": None, "preexisting": len(existing),
              "created": {"project": 0, "folders": 0, "notes": 0, "questions": 0,
                          "decisions": 0, "drafts": 0, "table": 0}}
    seed_fx_sample(report)
    try:
        seed_dev_project(api_base, report)
    except (urllib.error.URLError, OSError, ValueError, KeyError) as exc:
        report["error"] = f"development project: {type(exc).__name__}: {exc}"
    # True when this call created anything at all, which is what a relaunch of an
    # already-seeded session reports as false; the projects are listed either way.
    report["seeded"] = any(report["created"].values())
    return report


def _identity(local_status, db_path, data_dir):
    """The session identity. Truthful about both adapters, never about secrets.

    ``live_ai`` and ``cloud_ai`` stay False: at startup cloud is off and no key
    ships, so nothing paid is reachable until the user saves a key, switches
    cloud on and consents in Settings. The local entry describes an
    optional real runtime on this Mac, and says whether it is running now.
    """
    return {
        "review_build": True,
        "sample_data": True,
        "live_ai": False,
        "cloud_ai": False,
        "adapter": ("mock (scripted fake provider) for cloud routes; local mode uses "
                    "this Mac's installed model when available"),
        "local_runtime": local_status,
        "model_bundled": False,
        "database": str(db_path),
        "data_dir": str(data_dir),
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "pid": os.getpid(),
    }


def _ignore_further_signals():
    """Make cleanup unconditional once it has started.

    A second SIGTERM or Ctrl+C must not interrupt stopping the owned model: the
    exit paths that matter are the first one and cleanup completing.
    """
    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        number = getattr(signal, name, None)
        if number is not None:
            try:
                signal.signal(number, signal.SIG_IGN)
            except (OSError, ValueError):
                pass


def _parent_alive(pid):
    if not pid:
        return True
    try:
        os.kill(pid, 0)
        return True
    except OSError as exc:
        return exc.errno == errno.EPERM


def main(argv=None):
    parser = argparse.ArgumentParser(description="Research Workbench review backend")
    parser.add_argument("--state-dir", required=True)
    parser.add_argument("--ready-file", required=True)
    parser.add_argument("--parent-pid", type=int, default=0)
    args = parser.parse_args(argv)

    state = Path(args.state_dir).resolve()
    state.mkdir(parents=True, exist_ok=True)
    # The workspace persists across launches, and Electron's single-instance
    # lock cannot see a backend orphaned by a force-killed shell. The backend
    # therefore owns the workspace itself: one exclusive lock, released only when
    # this process exits. A waiting backend whose own shell has gone gives up.
    workspace_lock = open(state / "backend.lock", "w")
    while True:
        try:
            fcntl.flock(workspace_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            break
        except BlockingIOError:
            if not _parent_alive(args.parent_pid):
                return 0
            time.sleep(0.25)
    data_dir = state / "data"
    # Resolved BEFORE the session HOME below, then pinned for this process: the
    # real user's own local-runtime file must remain reachable once Path.home()
    # points into the session. The private HOME still governs every credential
    # reader, so "nothing the owner already has on the machine is read" still
    # holds for credentials, and the only owner-owned file read is the one that
    # names the paths of a model the owner installed.
    _resolve_local_config_path()
    # The workspace has its own HOME. The provider credential readers use
    # fixed paths under Path.home(), so this is what makes "no credential ships
    # and none is inherited" true: a key typed into this build's Settings is
    # written under this workspace (kept across launches) and nothing the owner
    # already has on the machine is read. Stray caches also stay inside it.
    home = state / "home"
    home.mkdir(parents=True, exist_ok=True)
    os.environ["HOME"] = str(home)
    if not (data_dir / "fx" / "permits_2023.csv").is_file():
        shutil.copytree(FIXTURE_DATA, data_dir, dirs_exist_ok=True)
    db_path = state / "review_session.db"

    if str(SITE_PACKAGES) not in sys.path:
        sys.path.insert(0, str(SITE_PACKAGES))
    sys.path.insert(0, str(APP_ROOT))

    # Review identity: mock transport only, and a loopback-only NO_PROXY so the
    # fake provider is never routed through a configured proxy.
    for name in LOCAL_RUN_ENV:
        os.environ.pop(name, None)
    # No machine credential is inherited. Every cloud reader in app/ai/secrets.py
    # prefers its environment override over the owner-only key file, so without
    # this a review session opened from a shell that exports META_API_KEY (or
    # BASETEN_API_KEY, OPENAI_API_KEY, MODEL_API_KEY, WB_MODAL_SHARED_API_KEY)
    # would silently run on the machine owner's credential — exactly what this
    # build promises not to do. The session HOME above already hides the key
    # files, so removing the overrides leaves no path to a pre-existing key, and
    # a key typed into this session's Settings is still saved and used.
    for name in CLOUD_KEY_ENV:
        os.environ.pop(name, None)
    os.environ["WB_ALLOW_REAL_MUSE"] = "0"
    os.environ["PYDANTIC_AI_NO_BANNER"] = "1"
    os.environ["NO_PROXY"] = "localhost,127.0.0.1"
    os.environ["no_proxy"] = "localhost,127.0.0.1"
    os.environ.setdefault("PYTHONDONTWRITEBYTECODE", "1")
    # Public, non-secret Modal Shared endpoint metadata. A packaged app has no
    # shell to export these in, so they are defaulted here; the credential is
    # still only what the user types into Settings in this session. Both values
    # are revalidated by app/ai/modal_shared_config.read_config() (https origin
    # on an exact Modal domain, and the pinned verified remote model id), and an
    # existing environment value always wins.
    os.environ.setdefault("WB_MODAL_SHARED_BASE_URL",
                          "https://maninspurs--ep-wb-research-spark-server.us-west.modal.direct")
    os.environ.setdefault("WB_MODAL_SHARED_MODEL_ID", "meta-models/Muse-Spark-Open-816B-A42B")

    uvicorn = importlib.import_module("uvicorn")
    fake_meta = importlib.import_module("app.fake_meta")
    api_module = importlib.import_module("app.api")

    fake_port, api_port, front_port = _free_port(), _free_port(), _free_port()
    os.environ["FAKE_BASE_URL"] = f"http://127.0.0.1:{fake_port}/v1"

    # The app's origin policy is a module-level list captured by reference by
    # both the CORS middleware and the two origin guards, so updating it in
    # place extends the accepted origin to this session's ports without a
    # second, wider rule. Only the two loopback spellings of one port are added.
    origin = f"http://127.0.0.1:{front_port}"
    api_module.ORIGINS[:] = [origin, f"http://localhost:{front_port}"]

    local_server = None
    static = None
    threads = []
    servers = []

    # Signals first: nothing that can spawn a child runs before the handlers are
    # installed, so an interrupt during model load reaches this process's
    # cleanup instead of leaving a loading child behind.
    def _terminate(*_):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _terminate)
    signal.signal(signal.SIGINT, _terminate)

    try:
        # The local model is started and verified BEFORE the API serves requests:
        # a request must never race a runtime that is still loading, and the
        # binding this publishes is what the API reads.
        local_server, local_status = _start_local_runtime(state, origin)
        if local_status["available"]:
            print(f"[backend] local model: {local_status['base_url']} (alias "
                  f"{local_status['model']}, context {local_status['context']}, template "
                  f"overhead {local_status['template_overhead_tokens']} tokens; local "
                  f"compute only, zero API cost)", flush=True)
        else:
            print(f"[backend] local model unavailable: {local_status['reason']}", flush=True)

        backend = api_module.create_app(db_path=str(db_path), data_dir=data_dir)

        fake_server = uvicorn.Server(uvicorn.Config(fake_meta.app, host="127.0.0.1",
                                                    port=fake_port, log_level="warning",
                                                    access_log=False))
        api_server = uvicorn.Server(uvicorn.Config(backend, host="127.0.0.1", port=api_port,
                                                   log_level="warning", access_log=False))
        servers = [fake_server, api_server]
        threads = [threading.Thread(target=fake_server.run, name="wb-fake", daemon=True),
                   threading.Thread(target=api_server.run, name="wb-api", daemon=True)]
        for thread in threads:
            thread.start()
        _wait_started(fake_server, "fake provider")
        _wait_started(api_server, "backend")

        static = _start_static(front_port, FRONTEND)
        api_base = f"http://127.0.0.1:{api_port}"
        # The workspace persists across launches, so the sample projects are added
        # once: a sample the user deleted must not come back on the next launch.
        seeded_marker = state / "sample_seeded.json"
        if seeded_marker.is_file():
            seed = {"seeded": False, "skipped": "sample projects were added on an earlier launch"}
        else:
            seed = _seed(api_base)
            if "error" not in seed and seed.get("projects"):
                seeded_marker.write_text(json.dumps(seed, indent=2) + "\n")

        app_url = f"{origin}/?api={api_base}&preview=1"
        identity = _identity(local_status, db_path, data_dir)
        (state / "REVIEW_SESSION.json").write_text(json.dumps(identity, indent=2) + "\n")
        # parent_pid lets the shell accept only its own backend's ready file.
        ready = {"app_url": app_url, "api": api_base,
                 "frontend": origin, "fake_provider": os.environ["FAKE_BASE_URL"],
                 "state_dir": str(state), "identity": identity, "seed": seed,
                 "parent_pid": args.parent_pid}
        # Atomic rendezvous: the Electron shell polls for the complete file only.
        temporary = Path(args.ready_file).with_suffix(".tmp")
        temporary.parent.mkdir(parents=True, exist_ok=True)
        temporary.write_text(json.dumps(ready, indent=2) + "\n")
        os.replace(temporary, args.ready_file)
        print(f"[backend] ready: {app_url}", flush=True)

        while _parent_alive(args.parent_pid):
            time.sleep(0.25)
    except KeyboardInterrupt:
        # SIGTERM from the shell's quit path, or Ctrl+C. Cleanup is below.
        pass
    finally:
        _ignore_further_signals()
        if static is not None:
            static.shutdown()
        for server in servers:
            server.should_exit = True
        for thread in threads:
            thread.join(timeout=10)
        # The model is stopped last, after the API that uses it, and through the
        # BaseException-safe helper: a model that cannot be stopped is reported
        # here rather than silently leaked or masked.
        if local_server is not None:
            _stop_local_runtime(local_server)
        try:
            Path(args.ready_file).unlink()
        except OSError:
            pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
