"""Seed the in-app development project, idempotently, over HTTP or in process.

Why this exists
---------------
The Workbench's own development is a real research project, and it is a more
convincing demonstration than a synthetic sample: the notes summarize this
repository's design documents, the decisions are decisions that were actually
taken, and the draft makes claims that the stored evidence either supports,
contradicts, or fails to reconcile. The content and its rules are described in
``data/dev_project/README.md``.

Two transports, one plan
------------------------
``data/dev_project/plan.json`` is the plan; the notes, the LaTeX draft and the
measurements CSV sit beside it as content. The same steps run either against a
running app (existing HTTP routes only, with the Origin header the frontend
sends) or directly against a database through the existing Harness, library and
latex functions. No route and no app module is added by this tool.

Idempotency is read back, never assumed
---------------------------------------
Every step first reads what is already stored and creates only what is missing:
projects by name, folders by name, notes and the results table by title inside
their folder, questions by their recorded text, decisions by whether that
question already carries one, drafts by title. A second run therefore creates
nothing, and a partially seeded project is completed instead of duplicated.

What the table step is allowed to do
------------------------------------
The measurements CSV becomes a library table. In process that is
``library.create_table(h, project_id, {"title", "csv_text"})``, which the numeric-check
work owns. Over HTTP it is exactly the route that work is adding,
``POST /projects/{pid}/library/tables`` with ``{"title", "csv_text"}``. Either is called
only when it exists; otherwise the step reports
``table skipped: create_table not available yet`` rather than inventing a table, and this
tool never writes a library row itself.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

PLAN_DIR = ROOT / "data" / "dev_project"
DATA_DIR = ROOT / "data"
FRONTEND_ORIGIN = "http://localhost:5173"
#: The exact report line for the one step this tool does not own.
TABLE_SKIP = "table skipped: create_table not available yet"
#: The route the engine work is adding for library tables. It is called when the
#: running app serves it and reported as skipped when it does not; this tool adds
#: no route of its own and no other path is tried.
TABLE_ROUTE = "/projects/{pid}/library/tables"


class Unreachable(RuntimeError):
    """The app could not be reached, or answered a step with an error."""


class TableUnavailable(Exception):
    """The library table step cannot run on this transport; the reason is reported."""


class RouteMissing(Unreachable):
    """The running app does not serve a route this tool would call if it existed.

    A 404 or a 405 on an exact path is how a FastAPI app answers a route that was
    never registered, which is the one case the seeder reports instead of failing:
    the table step belongs to another worker's route and may not have landed yet.
    Any other route missing is a real error, so this stays an ``Unreachable``.
    """


# ------------------------------------------------------------------ the plan
def load_plan(plan_dir=PLAN_DIR) -> dict:
    """The plan, with every file it names read as text under ``_text``.

    Reading the plan and its content together keeps the two from drifting: a
    note that the plan lists but the folder does not hold is an error here, not
    a silently missing note in the app.
    """
    plan_dir = Path(plan_dir)
    plan = json.loads((plan_dir / "plan.json").read_text(encoding="utf-8"))
    texts = {"csv": (plan_dir / plan["project"]["csv_file"]).read_text(encoding="utf-8")}
    for note in plan["notes"]:
        texts[note["file"]] = (plan_dir / note["file"]).read_text(encoding="utf-8")
    texts[plan["draft"]["file"]] = (plan_dir / plan["draft"]["file"]).read_text(encoding="utf-8")
    return {**plan, "plan_dir": str(plan_dir), "texts": texts}


# ------------------------------------------------------------------ transports
def _create_table():
    """The library table creator, if the numeric-check work has landed it."""
    from app import library

    return getattr(library, "create_table", None)


class HttpTransport:
    """A running app, through the routes it already serves."""

    def __init__(self, base_url, origin=FRONTEND_ORIGIN, timeout=60):
        self.base = base_url.rstrip("/")
        self.origin = origin
        self.timeout = timeout

    def _call(self, method, path, payload=None):
        data = None if payload is None else json.dumps(payload).encode()
        request = urllib.request.Request(
            self.base + path, data=data, method=method,
            headers={"Content-Type": "application/json", "Origin": self.origin})
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        try:
            with opener.open(request, timeout=self.timeout) as response:
                return json.loads(response.read().decode())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            if exc.code in (404, 405):
                raise RouteMissing(f"{method} {path} answered {exc.code}") from exc
            raise Unreachable(f"{method} {path} answered {exc.code}: {detail}") from exc
        except (urllib.error.URLError, OSError, ValueError) as exc:
            raise Unreachable(f"{method} {path} failed: {type(exc).__name__}: {exc}") from exc

    def projects(self):
        return self._call("GET", "/projects")["projects"]

    def create_project(self, name, csv_text, dictionary):
        return self._call("POST", "/projects/upload",
                          {"name": name, "csv_text": csv_text, "dictionary": dictionary})

    def tree(self, project_id):
        query = urllib.parse.urlencode({"project_id": project_id})
        return self._call("GET", f"/library/tree?{query}")

    def create_folder(self, project_id, name):
        return self._call("POST", "/library/folders", {"project_id": project_id, "name": name})

    def create_note(self, project_id, folder_id, title, text):
        return self._call("POST", "/library/notes",
                          {"project_id": project_id, "folder_id": folder_id,
                           "title": title, "text": text})

    def questions(self, project_id):
        """Questions and their decisions, read from this project's own events.

        Both are recorded there with the question they belong to, so one read
        tells the seeder what exists without a second route.
        """
        events = self._call("GET", f"/projects/{project_id}/events")["events"]
        return _fold_events(events)

    def ask(self, project_id, text):
        return self._call("POST", f"/projects/{project_id}/questions", {"text": text})

    def decide(self, project_id, question_id, verdict, reason):
        return self._call("POST", f"/projects/{project_id}/decisions",
                          {"question_id": question_id, "verdict": verdict, "reason": reason})

    def drafts(self, project_id):
        return self._call("GET", f"/projects/{project_id}/drafts")["drafts"]

    def create_draft(self, project_id, title, name, text):
        return self._call("POST", f"/projects/{project_id}/drafts",
                          {"title": title, "import": {"name": name, "text": text}})

    def create_table(self, project_id, title, csv_text):
        """POST the table to the route the engine work adds, when the app serves it.

        The path and the body are fixed by that work: ``POST
        /projects/{pid}/library/tables`` with ``title`` and ``csv_text``. A 404 or
        405 means the route has not landed in this build, which is reported, not
        worked around: no second path and no client-side table writer exists.
        """
        path = TABLE_ROUTE.format(pid=urllib.parse.quote(project_id))
        try:
            return self._call("POST", path, {"title": title, "csv_text": csv_text})
        except RouteMissing as exc:
            raise TableUnavailable(TABLE_SKIP) from exc


class HarnessTransport:
    """The same steps in this process, through the existing app functions."""

    def __init__(self, db_path, data_dir=DATA_DIR):
        from app.harness import Harness
        from app.offline import OfflineAdapter

        # Offline by construction: seeding a database must never spend a call.
        self.h = Harness(str(db_path), Path(data_dir), adapter=OfflineAdapter())

    def close(self):
        self.h.db.close()

    def projects(self):
        return [dict(r) for r in self.h.db.execute("SELECT id, name, kind, pack_id FROM projects")]

    def create_project(self, name, csv_text, dictionary):
        return self.h.register_upload(name, csv_text, dictionary)

    def tree(self, project_id):
        from app import library

        return library.tree(self.h, project_id)

    def create_folder(self, project_id, name):
        from app import library

        return library.create_folder(self.h, project_id, name)

    def create_note(self, project_id, folder_id, title, text):
        from app import library

        return library.create_note(self.h, {"project_id": project_id, "folder_id": folder_id,
                                           "title": title, "text": text})

    def questions(self, project_id):
        rows = self.h.db.execute("SELECT question_id, kind, payload FROM events WHERE project_id=?"
                                 " ORDER BY id", (project_id,)).fetchall()
        return _fold_events([dict(r) for r in rows])

    def ask(self, project_id, text):
        return asyncio.run(self.h.create_question(project_id, text))

    def decide(self, project_id, question_id, verdict, reason):
        return self.h.decide(project_id, question_id, verdict, reason, None)

    def drafts(self, project_id):
        from app import latex

        return latex.list_drafts(self.h, project_id)["drafts"]

    def create_draft(self, project_id, title, name, text):
        from app import latex

        return latex.create_draft(self.h, project_id, {"title": title,
                                                      "import": {"name": name, "text": text}})

    def create_table(self, project_id, title, csv_text):
        create_table = _create_table()
        if not callable(create_table):
            raise TableUnavailable(TABLE_SKIP)
        return create_table(self.h, project_id, {"title": title, "csv_text": csv_text})


def _fold_events(events) -> dict:
    """``{original question text: {"question_id", "decided"}}`` from event rows.

    A question's text and its decision are recorded on separate events that carry
    the same question id, so the fold is by id and the key is the text a person
    would recognize.
    """
    by_id = {}
    for event in events:
        payload = event.get("payload")
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except ValueError:
                continue
        if not isinstance(payload, dict):
            continue
        qid = event.get("question_id")
        if not qid:
            continue
        entry = by_id.setdefault(qid, {"question_id": qid, "text": None, "decided": False})
        if event.get("kind") == "question_captured" and payload.get("original_text"):
            entry["text"] = payload["original_text"]
        elif event.get("kind") == "decision_committed" and payload.get("verdict"):
            entry["decided"] = True
    return {entry["text"]: entry for entry in by_id.values() if entry["text"]}


# ------------------------------------------------------------------ the steps
def seed(transport, plan) -> dict:
    """Apply the plan through `transport`, creating only what is already missing."""
    name = plan["project"]["name"]
    texts = plan["texts"]
    created = {"project": 0, "folders": 0, "notes": 0, "questions": 0, "decisions": 0,
               "drafts": 0, "table": 0}
    report = {"project_id": None, "project": name, "created": created, "table": None}

    existing = [p for p in transport.projects() if p["name"] == name]
    if existing:
        project_id = existing[0]["id"]
    else:
        project_id = transport.create_project(name, texts["csv"],
                                              plan["project"]["dictionary"])["project_id"]
        created["project"] += 1
    report["project_id"] = project_id

    tree = transport.tree(project_id)
    folder_ids = {folder["name"]: folder["id"] for folder in tree["folders"]}
    for folder in plan["folders"]:
        if folder not in folder_ids:
            folder_ids[folder] = transport.create_folder(project_id, folder)["id"]
            created["folders"] += 1

    notes = {(item["folder_id"], item["title"]) for item in tree["items"] if item["kind"] == "note"}
    for note in plan["notes"]:
        folder_id = folder_ids[note["folder"]]
        if (folder_id, note["title"]) in notes:
            continue
        transport.create_note(project_id, folder_id, note["title"], texts[note["file"]])
        created["notes"] += 1

    questions = transport.questions(project_id)
    for decision in plan["decisions"]:
        entry = questions.get(decision["question"])
        if entry is None:
            entry = transport.ask(project_id, decision["question"])
            created["questions"] += 1
            question_id = entry.get("question_id")
        else:
            question_id = entry["question_id"]
        if not entry.get("decided"):
            transport.decide(project_id, question_id, decision["verdict"], decision["reason"])
            created["decisions"] += 1

    titles = {draft["title"] for draft in transport.drafts(project_id)}
    if plan["draft"]["title"] not in titles:
        draft_path = plan["draft"]["file"]
        transport.create_draft(project_id, plan["draft"]["title"],
                               Path(draft_path).name, texts[draft_path])
        created["drafts"] += 1

    table_title = plan["table"]["title"]
    if any(item["kind"] == "table" and item["title"] == table_title for item in tree["items"]):
        report["table"] = "table already present"
    else:
        try:
            transport.create_table(project_id, table_title, texts["csv"])
        except TableUnavailable as exc:
            report["table"] = str(exc)
        else:
            created["table"] += 1
            report["table"] = "table created"
    report["created_total"] = sum(created.values())
    return report


def seed_http(base_url, plan=None, origin=FRONTEND_ORIGIN) -> dict:
    return seed(HttpTransport(base_url, origin=origin), plan or load_plan())


def seed_database(db_path, plan=None, data_dir=DATA_DIR) -> dict:
    transport = HarnessTransport(db_path, data_dir)
    try:
        return seed(transport, plan or load_plan())
    finally:
        transport.close()


# ------------------------------------------------------------------ command line
def source_database(environ=None) -> Path:
    """The source app's own rehearsal database: ``WB_DB``, else local_rehearsal.db."""
    environ = os.environ if environ is None else environ
    return Path(environ.get("WB_DB") or (ROOT / "local_rehearsal.db"))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Seed the in-app development project, idempotently.")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--base-url", help="a running app, for example http://127.0.0.1:8123")
    target.add_argument("--db", help="a database file to seed in process")
    target.add_argument("--source-db", action="store_true",
                        help="opt in to seeding the source app's own rehearsal database")
    parser.add_argument("--origin", default=FRONTEND_ORIGIN, help="Origin header to send over HTTP")
    parser.add_argument("--data-dir", default=str(DATA_DIR), help="data directory for --db")
    parser.add_argument("--plan-dir", default=str(PLAN_DIR), help="directory holding plan.json")
    args = parser.parse_args(argv)

    plan = load_plan(args.plan_dir)
    try:
        if args.base_url:
            report = seed_http(args.base_url, plan, origin=args.origin)
        elif args.source_db:
            report = seed_database(source_database(), plan, Path(args.data_dir))
        else:
            report = seed_database(args.db, plan, Path(args.data_dir))
    except Unreachable as exc:
        print(json.dumps({"project": plan["project"]["name"], "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
