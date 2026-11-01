"""The in-app development project: its content rules, its seeder, and the packaged seed.

Three things are pinned here, and nothing else:

1. The authored content under ``data/dev_project/`` is public-safe. The exclusion
   greps below are the contract: no credential or credential path, no absolute
   local path, nothing from the private research folder, no private research data,
   and no claim the build has not earned.
2. ``tools/seed_dev_project.py`` seeds that content idempotently, in process
   against a temporary database and over HTTP against a loopback app, creating
   nothing on a second run.
3. ``_seed`` in ``packaging/backend_server.py`` produces both projects (the
   synthetic FX sample and the development project) in a fresh temporary state,
   and is idempotent on a relaunch of the same session.

The draft's claims are checked against the stored notes and the results table the
seed writes, so the Claim-Evidence demo's expected verdicts are asserted rather
than described. Every database here is temporary; the source app's own database is
never opened. No model call, no cloud credential, no loopback socket outside the
one HTTP test's own app.

This file is itself scanned by a repository-wide leak grep, so the forbidden
strings it tests for are assembled from fragments and never written out whole.
"""
import importlib.util
import json
import re
import shutil
import sys
import threading
import time
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import claim_check, latex, library, numeric_check  # noqa: E402
from app.ai import local_runtime, provider  # noqa: E402
from app.api import create_app  # noqa: E402
from app.harness import Harness  # noqa: E402
from app.offline import OfflineAdapter  # noqa: E402

TOOL = ROOT / "tools" / "seed_dev_project.py"
BACKEND = ROOT / "packaging" / "backend_server.py"
CONTENT = ROOT / "data" / "dev_project"
PROJECT_NAME = "Research Workbench - hackathon build"
FAQ_TITLE = "Hackathon FAQ (as provided by the participant)"
DRAFT_TITLE = "Submission write-up - draft"
EM_DASH = "\u2014"

#: The demo's claim-check inputs: a one-sentence Korean claim, which is the case
#: that costs the most bytes per character against the local model's context. The
#: output reservation is read from the request schema, never retyped here.
KOREAN_CLAIM = "이 노트의 수치가 이 기기의 실제 측정값인지 확인해 주세요."
CLAIM_CHECK_DEFAULT_OUTPUT_TOKENS = claim_check.ClaimCheckRequest.model_fields[
    "max_output_tokens"].default

# Fragments, joined at import: a repository-wide grep for the whole literal must
# find nothing in this file, so no forbidden string appears here in one piece.
PRIVATE_FOLDER = "NYC" + "_97"
CLOUD_ROOT = "Drop" + "box"
HOME_ROOT = "/Us" + "ers/"
TILDE_ROOT = "~" + "/"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses resolve annotations through sys.modules
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(spec.name, None)
        raise
    return module


@pytest.fixture(scope="module")
def seeder():
    try:
        yield load(TOOL, "seed_dev_project_under_test")
    finally:
        sys.modules.pop("seed_dev_project_under_test", None)


@pytest.fixture(scope="module")
def packaged():
    try:
        yield load(BACKEND, "backend_server_under_test")
    finally:
        sys.modules.pop("backend_server_under_test", None)


def test_the_packaged_fx_sample_name_matches_the_harness(packaged):
    """The packaged seed finds the sample by name; a drifted copy reseeds it on every launch."""
    from app.harness import PROJECT_KINDS

    assert packaged.FX_SAMPLE_NAME == PROJECT_KINDS["fx"]["name"]


@pytest.fixture()
def temp_state(tmp_path):
    """A throwaway copy of the sample data, so nothing writes into the tree."""
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    return tmp_path, data


def authored_texts():
    """Every authored file as (name, text), whatever its suffix.

    The walk is unfiltered on purpose: the exclusion greps have to see every file
    that is actually in the folder, not only the ones the plan lists, so a stray
    file cannot hide from them.
    """
    return [(path.relative_to(CONTENT).as_posix(), path.read_text(encoding="utf-8"))
            for path in sorted(CONTENT.rglob("*")) if path.is_file()]


#: The only file types this content may contain. Anything else would become
#: trackable under the .gitignore negation and could carry private material.
ALLOWED_SUFFIXES = {".json", ".md", ".tex", ".csv"}


def test_every_file_in_the_content_folder_is_an_allowed_type():
    """A walk of the whole folder: an unexpected file type is a failure, not a pass.

    The .gitignore now un-ignores ``data/dev_project/`` so this folder ships as
    source. That makes an unexpected file type here a leak risk: a PDF, an image,
    a database or an archive dropped into the folder would be shippable. Only the
    four types the seed needs are accepted, and every one is listed by the plan or
    is the README.
    """
    files = sorted(path for path in CONTENT.rglob("*") if path.is_file())
    assert files, "the content folder is empty"
    unexpected = [path.relative_to(CONTENT).as_posix() for path in files
                  if path.suffix.lower() not in ALLOWED_SUFFIXES]
    assert unexpected == [], f"unexpected file types in the content: {unexpected}"
    # Nothing hidden, and nothing nested deeper than the plan's own layout uses.
    assert [p.relative_to(CONTENT).as_posix() for p in files if p.name.startswith(".")] == []
    assert {p.relative_to(CONTENT).parts[0] for p in files} <= {
        "README.md", "plan.json", "measurements.csv", "notes", "draft"}


def test_the_plan_names_every_file_the_seed_needs():
    """Every file the seed reads is declared, so plan and content cannot drift."""
    plan = json.loads((CONTENT / "plan.json").read_text(encoding="utf-8"))
    declared = {plan["project"]["csv_file"], plan["table"]["csv_file"], plan["draft"]["file"]}
    declared |= {note["file"] for note in plan["notes"]}
    on_disk = {p.relative_to(CONTENT).as_posix() for p in CONTENT.rglob("*") if p.is_file()}
    assert declared <= on_disk, declared - on_disk
    # The files present but not read by the seed are exactly the README and the plan.
    assert on_disk - declared == {"README.md", "plan.json"}


def harness_for(db_path, data_dir):
    """A harness on a temporary database, offline by construction: no call is made."""
    return Harness(str(db_path), Path(data_dir), adapter=OfflineAdapter())


# ------------------------------------------------------------------ the content
def test_the_content_the_plan_names_is_present():
    plan = json.loads((CONTENT / "plan.json").read_text(encoding="utf-8"))
    assert plan["project"]["name"] == PROJECT_NAME
    assert plan["folders"] == ["Design", "Hackathon rules", "Decisions", "Model notes"]
    assert (CONTENT / plan["draft"]["file"]).is_file()
    assert (CONTENT / plan["project"]["csv_file"]).is_file()
    for note in plan["notes"]:
        assert (CONTENT / note["file"]).is_file(), note["file"]
        assert note["folder"] in plan["folders"]
    # The decisions are the real ones, each with the question that produced it,
    # because the app's decision mechanism records a decision against a question.
    assert len(plan["decisions"]) == 5
    assert all(d["question"].strip().endswith("?") for d in plan["decisions"])
    assert all(d["verdict"] in ("adopt", "defer", "reject") for d in plan["decisions"])
    assert any(d["question"] == "Can a local 4B model give zero-cost research help?"
               for d in plan["decisions"])


def test_the_faq_note_carries_only_the_participant_provided_facts():
    text = (CONTENT / "notes" / "hackathon_faq.md").read_text(encoding="utf-8")
    assert text.splitlines()[0] == FAQ_TITLE
    assert "participant-provided summary" in text
    # The four facts the brief allows, each stated once.
    assert "Meta Model API" in text and "Meta open-weight models" in text
    assert "25%" in text and "impact" in text and "technical depth" in text
    assert "Nov 4" in text and "9:00 PM ET" in text
    assert "3-minute demo video" in text
    assert "public repository" in text and "contributors" in text
    # The weights are attributed to the shared FAQ text, worded as the participant's
    # source states it, and the repository's earlier NOT_STATED marking is named so
    # the change of record is visible rather than silent.
    joined = " ".join(text.split())
    assert ("According to the official Discord FAQ text the participant shared on "
            "2026-10-31, the four criteria are weighted equally (25% each).") in joined
    assert "NOT_STATED" in text
    # And nothing beyond them: nothing quoted from an organizer, no invented rule,
    # and no date other than the deadline and the date the shared text was sent.
    assert '"' not in text and "\u201c" not in text
    assert re.findall(r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\.? \d+",
                      text) == ["Nov 4"]
    assert re.findall(r"\b\d{4}-\d{2}-\d{2}\b", text) == ["2026-10-31"]


EXCLUSIONS = (
    # No key material and no name of one: nothing a reader could mistake for a
    # credential, and no variable that holds one.
    ("credential", re.compile(r"api[ _-]?key|secret[ _-]?key|private[ _-]?key|sk-[A-Za-z0-9]|"
                              r"\.env\b|credentials?\.json|Authorization:|API_KEY|APIKEY", re.I)),
    # No absolute local path of any machine.
    ("absolute path", re.compile(re.escape(HOME_ROOT) + r"|/home/[A-Za-z]|" + re.escape(TILDE_ROOT)
                                 + r"|/private/|/Volumes/|[A-Za-z]:\\\\|file://")),
    # Nothing from, or naming, the private folder.
    ("private folder", re.compile(re.escape(CLOUD_ROOT) + r"|cloudstorage|"
                                  + re.escape(PRIVATE_FOLDER), re.I)),
    # No private research material and no real-world personal data.
    ("private data", re.compile(r"@[A-Za-z0-9-]+\.[A-Za-z]{2,}|consent form|participant data|"
                                r"interview transcript", re.I)),
)


@pytest.mark.parametrize("label,pattern", EXCLUSIONS, ids=[label for label, _ in EXCLUSIONS])
def test_authored_content_passes_its_exclusion_grep(label, pattern):
    """The rule the README states, verified by grep over every authored byte."""
    hits = [f"{name}: {match.group(0)!r}"
            for name, text in authored_texts() for match in [pattern.search(text)] if match]
    assert hits == [], f"{label} appears in the development-project content: {hits}"


def test_the_content_carries_no_credential_shaped_literal():
    """A second grep for the thing itself: a token-like value, not a token's name.

    A note may *describe* the app generating its own per-run credential for the
    model server it starts, and it may cite a design document by file name. What
    no file may contain is a value that could be a credential: a hex digest, or a
    mixed-case run carrying digits (the shape a key or a session id has). File
    names are removed first, because a cited document is evidence, not a secret.
    """
    citation = re.compile(r"[A-Za-z0-9_.\-]+\.(?:md|tex|csv|json|py)\b")
    shapes = re.compile(r"\b(?:[0-9a-f]{32,}|(?=[A-Za-z0-9_\-]*[A-Z])(?=[A-Za-z0-9_\-]*[a-z])"
                        r"(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{24,})\b")
    hits = [f"{name}: {match.group(0)!r}"
            for name, text in authored_texts()
            for match in shapes.finditer(citation.sub("", text))]
    assert hits == [], f"credential-shaped literal in the content: {hits}"


def test_the_plan_and_its_content_name_no_local_machine_path():
    """A second pass over the plan's own strings, including JSON keys and values."""
    plan = json.loads((CONTENT / "plan.json").read_text(encoding="utf-8"))
    serialized = json.dumps(plan)
    for pattern in (re.escape(HOME_ROOT), re.escape(TILDE_ROOT), re.escape(CLOUD_ROOT), "cloudstorage"):
        assert re.search(pattern, serialized, re.I) is None, pattern


def test_the_content_is_english_screen_text():
    """English screen text, except the explicitly allowed author-reviewer name."""
    offenders = [name for name, text in authored_texts()
                 if re.search(r"[\uac00-\ud7a3\u1100-\u11ff]", text.replace("Author", "").replace(
                     "AI 1차 채점 → 사람 2차 검수(출처 공개 뒤)", ""))]
    assert offenders == []


def test_the_draft_states_what_it_has_not_earned():
    """The honest markers stay, and the shipped feature is labelled as shipped.

    The Claim-Evidence check has shipped, so the draft must say so and cite the two
    files that carry it, while keeping every marker that is still true: the cloud
    claim check ran live on Baseten, the comparison ran with two-pass scoring,
    and preregistered aggregates remain NOT_SCORED. Neither an
    old "not implemented" nor a stale "no paid call has been made" can creep back in.
    """
    text = (CONTENT / "draft" / "main.tex").read_text(encoding="utf-8")
    flat_text = " ".join(text.split())
    assert "NOT_RUN" in text
    assert "NOT_SCORED" in text
    assert "does not claim model quality" in text
    # Shipped, and named by the files that carry it.
    assert "implemented and independently" in text
    assert "app/claim_check.py" in text or r"app/claim\_check.py" in text
    assert "frontend/claim_check_ui.js" in text or r"frontend/claim\_check\_ui.js" in text
    # The old wording is gone: it is neither unbuilt nor withheld from the build.
    assert "IN_PROGRESS" not in text
    assert "SAM mask raster decoding" in flat_text and "NOT_IMPLEMENTED" in text
    assert "not shipped in the" not in text
    # The paths are distinguished: the cloud claim check ran live, the local path ran
    # live, separately from the later research comparison.
    assert "live" in text
    assert "Baseten Muse Spark Open" in text
    assert "aichk-d6f42801700a" in text
    assert "3.4 s" in text
    assert "0.004114 USD" in text
    assert "2026-10-31" in text
    # The stale claim that the cloud path never ran may not return.
    assert "mock transport only" not in text
    assert "no paid call has been made" not in text
    assert "live cloud quality or live cloud cost" not in text
    # One live supported verdict is not a quality measurement, and costs are computed.
    assert "calculated" in text
    assert "not invoice" in flat_text or "read off an invoice" in flat_text
    # A returned reading is a suggestion the researcher decides on, not a ruling.
    assert "secretary" in text
    assert "suggestion" in text
    assert "verbatim" in text
    assert "excerpt actually sent" in flat_text
    assert "consent" in text
    assert "never a ruling that a sentence is true" in flat_text
    # Formerly the confirming step was "human review"; the framing is now the
    # researcher's own decision, so that phrasing may not return.
    assert "human review" not in text


def test_the_quotes_the_draft_accuses_are_quoted_from_the_notes():
    """The contradicting claim is checkable: the note and the draft agree on it."""
    note = (CONTENT / "notes" / "design_claim_evidence.md").read_text(encoding="utf-8")
    flat_note = " ".join(note.split())
    # The note is honest about status in the same way the draft is.
    assert "implemented and independently reviewed" in note
    assert "app/claim_check.py" in note and "frontend/claim_check_ui.js" in note
    assert "live on Baseten Muse Spark Open" in flat_note
    assert "aichk-d6f42801700a" in note
    assert "research comparison ran on 2026-11-01" in flat_note
    assert "mock transport" not in note
    assert "IN_PROGRESS" not in note and "NOT_IMPLEMENTED" not in note
    assert "verbatim" in note
    assert "secretary" in note
    assert "never a ruling that a sentence is true" in flat_note


def test_the_shipped_and_unshipped_paths_are_stated_separately_everywhere():
    """One rule, checked on every file that describes the check's status.

    Implemented features, the live cloud claim check and the live local measurement
    are three different claims, and the later research comparison is a fourth.
    Any file that describes the check has to keep them apart, so a build that
    ships one path cannot read as if it shipped all of them, and a path that has run
    cannot be described as mock-only.
    """
    observed = "Lead-observed live run, 2026-10-31 (log outside repository)"
    files = {
        "draft/main.tex": (CONTENT / "draft" / "main.tex").read_text(encoding="utf-8"),
        "README.md": (CONTENT / "README.md").read_text(encoding="utf-8"),
        "notes/design_claim_evidence.md":
            (CONTENT / "notes" / "design_claim_evidence.md").read_text(encoding="utf-8"),
        "notes/decisions_release_gate.md":
            (CONTENT / "notes" / "decisions_release_gate.md").read_text(encoding="utf-8"),
        "notes/local_model.md": (CONTENT / "notes" / "local_model.md").read_text(encoding="utf-8"),
    }
    flat = {name: " ".join(text.split()) for name, text in files.items()}

    decision = json.loads((CONTENT / "plan.json").read_text(encoding="utf-8"))["decisions"]
    reason = next(d for d in decision if d["key"] == "claim-evidence-core")["reason"]
    flat["plan.json:claim-evidence-core"] = " ".join(reason.split())

    for name, text in flat.items():
        assert "2026-11-01" in text, name
        assert "n=1 per case-arm" in text, name
        assert "AI first-pass scoring, human second-pass review (after unblinding)" in text, name
        assert "Preregistered aggregates remain NOT_SCORED" in text, name
        assert "implemented" in text.lower(), f"{name} does not state what is implemented"
        # The live measurements are named with their date.
        assert "2026-10-31" in text, f"{name} does not date the live runs"
        # The cloud claim check is live, not mock-only, and the dead wording is gone.
        assert "mock transport" not in text, f"{name} still calls the cloud path mock-only"
    # The live cloud claim check is named with its job id and its cost where the
    # numbers live, distinct from the research comparison.
    for where in ("draft/main.tex", "README.md", "notes/design_claim_evidence.md",
                  "notes/decisions_release_gate.md"):
        assert "aichk-d6f42801700a" in flat[where], where
        assert "Baseten" in flat[where], where
    # The two live local claim-check jobs appear where the numbers live, with the
    # same provenance label the other measurements use.
    for where in ("README.md", "notes/local_model.md"):
        assert "aichk-7b84a5395773" in flat[where], where
        assert "aichk-79e4909e85e6" in flat[where], where
        assert "7.97" in flat[where] and "11.09" in flat[where], where
    assert observed in flat["notes/local_model.md"]
    # Every cost is a calculation, never an invoice figure.
    for where in ("README.md", "notes/design_claim_evidence.md"):
        assert "calculated" in flat[where], where
    # And nowhere does the seeded text still call the check unbuilt. The marker
    # names themselves stay (the ledger still has NOT_IMPLEMENTED features), so
    # this looks for the stale status statement, not the vocabulary.
    for name, text in flat.items():
        assert "is IN_PROGRESS" not in text, name
        assert "being built" not in text, name
        assert "with human review" not in text, name

    # Scan all authored content, not just the files describing the claim check.
    for name, text in authored_texts():
        joined = " ".join(text.split())
        assert not re.search(r"comparison[^.!?\n]*NOT_RUN", joined, re.I), name
        assert not re.search(r"human scoring[^.!?\n]*NOT_SCORED", joined, re.I), name
        assert "blinded human scoring" not in joined.lower(), name
        assert not re.search(r"comparison[^.!?\n]*has not run", joined, re.I), name

    for where in ("draft/main.tex", "notes/hackathon_ledger.md"):
        text = " ".join((CONTENT / where).read_text(encoding="utf-8").split())
        assert "GPT-6.1-Sol medium" in text
        # The comparison's own model, pinned so a swapped id cannot pass silently.
        assert "meta-models/muse-spark-open-1026" in text
        assert "36 claims and 80 citation occurrences" in text
        assert "changing no marks" in text
        assert "not blinded or independent" in text
        assert "All 9 Baseten calls completed" in text and "0.0722 USD" in text
        assert "not actual final cost" in text
        assert "indication, not an established harness effect" in text
        assert "thresholds never set" in text
        for case, ratings in (
            ("FX-COST-1", (4, 4, 4, 4)),
            ("FX-APPROVAL-1", (4, 5, 4, 3)),
            ("NYC-DATE-1", (3, 4, 2, 3)),
        ):
            separator = " & " if where.endswith(".tex") else " | "
            assert separator.join([case, *map(str, ratings)]) in text


def test_current_round3_result_preserves_ai_human_distinction_and_spend():
    for name in ("README.md", "notes/hackathon_ledger.md", "draft/main.tex"):
        text = (CONTENT / name).read_text(encoding="utf-8")
        assert "AI 1차 채점 → 사람 2차 검수(출처 공개 뒤)" in text
        assert "Human round-3 review is pending" in text
        assert "56/60" in text
        for cost in ("0.13738895", "0.21011545", "0.20959970", "4.16838745"):
            assert cost in text
        separator = " & " if name.endswith(".tex") else " | "
        for provider, basic, harness in (
                ("Baseten", "9/10", "9/10"),
                ("Local Qwen", "4/10", "6/10"),
                ("Modal Shared", "7/10", "7/10")):
            assert separator.join((provider, basic, harness)) in text


def test_the_reasoning_effort_decision_records_its_evidence():
    """The benchmark chose low over none, and the record says why, with both medians."""
    plan = json.loads((CONTENT / "plan.json").read_text(encoding="utf-8"))
    decision = next(d for d in plan["decisions"] if d["key"] == "keep-reasoning-effort-low")
    assert decision["verdict"] == "adopt"
    reason = " ".join(decision["reason"].split())
    # Both medians, both ranges' endpoints, and the cost of each arm.
    assert "2.55" in reason and "2.11" in reason
    assert "2.32" in reason and "4.04" in reason
    assert "1.91" in reason and "2.61" in reason
    assert "0.45" in reason
    assert "0.0151" in reason and "0.0126" in reason
    # The reasoning for keeping low: the gap is not a real difference, and caching
    # favored the cheaper arm rather than the faster one.
    assert "inside run-to-run spread" in reason
    assert "caching" in reason
    assert "0.45 s gap" in reason
    # Both arms produced clean notes, and the costs are computed.
    assert "10 calls" in reason
    assert "verified quote" in reason
    assert "calculated, not invoice-verified" in reason
    # And the release-gate note carries the same decision.
    note = " ".join((CONTENT / "notes" / "decisions_release_gate.md")
                    .read_text(encoding="utf-8").split())
    assert "Reasoning effort stays low" in note
    assert "2.55" in note and "2.11" in note


def test_no_em_dashes_in_the_authored_content_or_this_tools_prose():
    """House style: U+2014 is not used in authored prose.

    En dashes and hyphens are untouched; this is only about the em dash. The
    scope is the content this work authored plus the two files it added, not
    pre-existing prose elsewhere in the tree.
    """
    offenders = [name for name, text in authored_texts() if EM_DASH in text]
    for path in (TOOL, Path(__file__).resolve()):
        if EM_DASH in path.read_text(encoding="utf-8"):
            offenders.append(path.relative_to(ROOT).as_posix())
    assert offenders == [], f"U+2014 in: {offenders}"


# ------------------------------------------------------- the claim–evidence plan
def claims_from_draft(text):
    """The draft's claim list, in reading order, from its one enumerate block."""
    body = text.split(r"\begin{enumerate}", 1)[1].split(r"\end{enumerate}", 1)[0]
    items, current = [], []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith(r"\item"):
            if current:
                items.append(" ".join(current))
            current = [stripped[len(r"\item"):].strip()]
        else:
            current.append(stripped)
    if current:
        items.append(" ".join(current))
    return [" ".join(item.split()) for item in items]


def seeded(h, project_id):
    """The stored notes and the stored results table of one seeded project."""
    tree = library.tree(h, project_id)
    notes = {item["title"]: item for item in tree["items"] if item["kind"] == "note"}
    tables = [item for item in tree["items"] if item["kind"] == library.TABLE_KIND]
    assert len(tables) == 1, "the seed writes exactly one results table"
    return notes, library.load_table(h, tables[0]["id"], project_id)


def test_the_draft_has_the_five_claim_kinds_the_demo_needs(seeder, temp_state):
    tmp, data = temp_state
    plan = seeder.load_plan(CONTENT)
    seeder.seed_database(tmp / "wb.db", plan, data)

    h = harness_for(tmp / "wb.db", data)
    try:
        project_id = h.db.execute("SELECT id FROM projects WHERE name=?", (PROJECT_NAME,)).fetchone()["id"]
        notes, table = seeded(h, project_id)
        draft_row = h.db.execute("SELECT id FROM latex_drafts WHERE project_id=? AND title=?",
                                 (project_id, DRAFT_TITLE)).fetchone()
        assert draft_row is not None
        draft = latex.read_draft(h, draft_row["id"])
        body = "\n".join(f["text"] for f in draft["files"])
    finally:
        h.db.close()

    claims = claims_from_draft(body)
    verdicts = {claim: numeric_check.reconcile(claim, table)["summary"] for claim in claims}

    # 1. Two claims a stored note clearly supports.
    local_note = notes["Local model: the pinned install"]["text"]
    supported = [c for c in claims if "costs nothing in API credits" in c
                 or "stops the local model server" in c]
    assert len(supported) == 2, claims
    assert "zero API cost" in local_note
    assert "stops the child on quit" in local_note

    # 2. One claim a stored note contradicts: grounding does not establish truth,
    #    and the note says so in the same words the draft rejects.
    refuted = [c for c in claims if "confirm that a sentence is true" in c]
    assert len(refuted) == 1
    assert "never a ruling that a sentence is true" in notes["Claim-Evidence verification"]["text"]

    # 3. The claim whose numbers the results table reconciles.
    measured = next(c for c in claims if "2,497,281,120" in c)
    assert verdicts[measured] == {"total": 3, "match": 3, "near_mismatch": 0, "not_found": 0}

    # 4. The claim with a deliberately wrong number: a real, visible mismatch.
    wrong = next(c for c in claims if "2,300" in c)
    assert verdicts[wrong]["near_mismatch"] == 1
    assert verdicts[wrong]["match"] == 0

    # And the honest counts reconcile exactly, including the range and the xfail.
    honest = next(c for c in claims if "2,224" in c)
    assert verdicts[honest] == {"total": 2, "match": 2, "near_mismatch": 0, "not_found": 0}
    latency = next(c for c in claims if "4.6 to 6.4" in c)
    assert verdicts[latency] == {"total": 2, "match": 2, "near_mismatch": 0, "not_found": 0}


def claim_check_prompt(note_text, claim_text=KOREAN_CLAIM, language="ko", *, title):
    """The exact local prompt the app sends for one note, built by the app itself.

    ``claim_check._body`` is the same builder prepare and execute use, and
    ``provider.local_prompt_text`` is the same flattener the local provider sends,
    so this is the bytes that reach llama-server rather than a copy of their size.
    The excerpt goes through ``claim_check._excerpt`` for the same reason: the app
    bounds the source text before it counts anything.
    """
    excerpt = claim_check._excerpt([note_text])
    record = {"model": local_runtime.MODEL_ID,
              "request": {"language": language,
                          "max_output_tokens": CLAIM_CHECK_DEFAULT_OUTPUT_TOKENS,
                          "reasoning_effort": None},
              "claim": {"text": claim_text},
              "sources": [{"item_id": "item-under-test", "title": title,
                           "excerpt": excerpt}]}
    return provider.local_prompt_text(claim_check._body(record))


def test_every_seeded_note_fits_the_local_context_for_a_korean_claim_check(seeder):
    """The demo's failure mode: a note too long for the launched context.

    A local claim check is refused before any model call when its prompt exceeds
    the budget at the app's declared context, so a seeded note that cannot be
    checked would break a demo that picked it. Every note must fit a one-sentence
    Korean claim on its own, with room to spare, and the size is measured by the
    app's own ``fit_report`` rather than by arithmetic repeated here.
    """
    notes = sorted((CONTENT / "notes").glob("*.md"))
    assert len(notes) == 6, notes
    context = 8192
    reports = {}
    titles = {Path(n["file"]).name: n["title"] for n in seeder.load_plan(CONTENT)["notes"]}
    for path in notes:
        prompt = claim_check_prompt(path.read_text(encoding="utf-8"), title=titles[path.name])
        fit = local_runtime.fit_report(prompt, context, None,
                                      CLAIM_CHECK_DEFAULT_OUTPUT_TOKENS)
        reports[path.name] = fit
        assert fit["fits"], f"{path.name}: {fit}"
    # Clear margin, not a hairline pass: the shortest margin still leaves room for
    # a longer claim sentence or a future edit to one of these notes.
    margins = {name: fit["max_prompt_bytes"] - fit["prompt_utf8_bytes"]
               for name, fit in reports.items()}
    assert min(margins.values()) >= 150, margins
    local_prompt = reports["local_model.md"]["prompt_utf8_bytes"]
    assert local_prompt <= 5500, local_prompt
    # And the binding itself is the shipped one, not a value invented here.
    assert reports["local_model.md"]["reserved_output_tokens"] == CLAIM_CHECK_DEFAULT_OUTPUT_TOKENS
    assert reports["local_model.md"]["context"] == context


def test_context_prompt_transmits_the_actual_seeded_title(seeder):
    note = seeder.load_plan(CONTENT)["notes"][0]
    prompt = claim_check_prompt((CONTENT / note["file"]).read_text(encoding="utf-8"),
                                title=note["title"])
    assert note["title"] in prompt


def test_the_results_table_holds_the_real_measured_numbers(seeder, temp_state):
    tmp, data = temp_state
    seeder.seed_database(tmp / "wb.db", seeder.load_plan(CONTENT), data)
    h = harness_for(tmp / "wb.db", data)
    try:
        project_id = h.db.execute("SELECT id FROM projects WHERE name=?", (PROJECT_NAME,)).fetchone()["id"]
        _, table = seeded(h, project_id)
    finally:
        h.db.close()
    values = {row[0]: row[1] for row in table["rows"]}
    assert table["columns"] == ["metric", "value", "unit", "source"]
    assert values["local_answer_seconds_min"] == "4.6"
    assert values["local_answer_seconds_max"] == "6.4"
    assert values["claim_check_local_seconds_min"] == "7.97"
    assert values["claim_check_local_seconds_max"] == "11.09"
    assert values["claim_check_cloud_seconds"] == "3.4"
    assert values["claim_check_cloud_cost_usd"] == "0.004114"
    assert values["reasoning_low_median_seconds"] == "2.55"
    assert values["reasoning_none_median_seconds"] == "2.11"
    assert values["reasoning_low_arm_cost_usd"] == "0.0151"
    assert values["reasoning_none_arm_cost_usd"] == "0.0126"
    assert values["reasoning_benchmark_total_cost_usd"] == "0.0277"
    assert values["template_overhead_tokens"] == "8"
    assert values["context_tokens"] == "8192"
    assert values["full_test_suite_passed"] == "2224"
    assert values["full_test_suite_xfailed"] == "1"
    assert values["local_weights_bytes"] == "2497281120"
    # The claim-check bands and the cloud benchmark are separate measurements from
    # the short-question band, so the table carries sixteen rows and the plan's
    # coverage entry says so.
    assert len(table["rows"]) == 16
    # The benchmark total is the sum of its two arms, so a typo in any of the three
    # rows shows up as an arithmetic mismatch in the table itself.
    assert round(float(values["reasoning_low_arm_cost_usd"])
                 + float(values["reasoning_none_arm_cost_usd"]), 4) == \
        float(values["reasoning_benchmark_total_cost_usd"])
    plan = json.loads((CONTENT / "plan.json").read_text(encoding="utf-8"))
    coverage = next(e for e in plan["project"]["dictionary"]["entries"]
                    if e["field"] == "(dataset coverage)")
    assert coverage["definition"].startswith("Sixteen rows")


def test_every_measurement_names_where_it_was_observed():
    """Provenance per row, in the words the review fixed, with no absolute path.

    The numbers were observed in Lead-run live checks on 2026-10-31 whose logs sit
    outside the repository, and the two constants come from the app's own local
    runtime module. Nothing here may be sourced to a machine-specific path.
    """
    import csv as csv_module
    import io

    rows = list(csv_module.DictReader(io.StringIO(
        (CONTENT / "measurements.csv").read_text(encoding="utf-8"))))
    observed = "Lead-observed live run, 2026-10-31 (log outside repository)"
    sources = {row["metric"]: row["source"] for row in rows}
    for metric in ("local_answer_seconds_min", "local_answer_seconds_max",
                   "claim_check_local_seconds_min", "claim_check_local_seconds_max",
                   "claim_check_cloud_seconds", "claim_check_cloud_cost_usd",
                   "reasoning_low_median_seconds", "reasoning_none_median_seconds",
                   "reasoning_low_arm_cost_usd", "reasoning_none_arm_cost_usd",
                   "reasoning_benchmark_total_cost_usd",
                   "template_overhead_tokens", "full_test_suite_passed",
                   "full_test_suite_xfailed"):
        assert sources[metric].startswith(observed), metric
    # The five live jobs, the packaged-DMG provenance of the slowest, and the
    # dated-snapshot wording for the suite count.
    for job in ("ai-60d36123b56f", "ai-96f536df03a5", "ai-b8297f813eb8",
                "ai-7a62cfdcd455", "ai-0e8e3734d1bf"):
        assert job in sources["local_answer_seconds_min"], job
        assert job in sources["local_answer_seconds_max"], job
    assert "packaged DMG app" in sources["local_answer_seconds_max"]
    assert "4.615 s" in sources["local_answer_seconds_min"]
    assert "6.37 s" in sources["local_answer_seconds_max"]
    # The two claim-check rows carry their own job ids and the fact that both were
    # free, and the faster one names the packaged DMG it ran in.
    assert "aichk-7b84a5395773" in sources["claim_check_local_seconds_min"]
    assert "aichk-79e4909e85e6" in sources["claim_check_local_seconds_max"]
    assert "packaged DMG app" in sources["claim_check_local_seconds_min"]
    assert "cost 0" in sources["claim_check_local_seconds_min"]
    assert "cost 0" in sources["claim_check_local_seconds_max"]
    assert "Korean" in sources["claim_check_local_seconds_min"]
    # The cloud claim check names its job, its endpoint, its token split and the
    # fact that the dollar figure is a calculation rather than an invoice.
    assert "aichk-d6f42801700a" in sources["claim_check_cloud_seconds"]
    assert "meta-models/muse-spark-open-1026" in sources["claim_check_cloud_seconds"]
    assert "Korean" in sources["claim_check_cloud_seconds"]
    assert "supported" in sources["claim_check_cloud_seconds"]
    assert "verbatim" in sources["claim_check_cloud_seconds"]
    assert "1105 input and 643 output tokens" in sources["claim_check_cloud_cost_usd"]
    assert "not invoice-verified" in sources["claim_check_cloud_cost_usd"]
    # Both benchmark arms state their sample size, their range and their arm cost,
    # and the total says it is the sum of the two.
    for metric, effort in (("reasoning_low_median_seconds", "low"),
                           ("reasoning_none_median_seconds", "none")):
        assert "5 alternating calls" in sources[metric], metric
        assert effort in sources[metric], metric
        assert "not invoice-verified" in sources[metric], metric
    assert "2.32 to 4.04" in sources["reasoning_low_median_seconds"]
    assert "1.91 to 2.61" in sources["reasoning_none_median_seconds"]
    assert "caching favored" in sources["reasoning_none_median_seconds"]
    assert "10" in sources["reasoning_benchmark_total_cost_usd"]
    for metric in ("full_test_suite_passed", "full_test_suite_xfailed"):
        assert "dated snapshot" in sources[metric], metric
        assert "not the current count" in sources[metric], metric
    # The two declared constants are sourced to the module that declares them.
    for metric in ("context_tokens", "local_weights_bytes"):
        assert "app/ai/local_runtime.py" in sources[metric], metric
    # No row carries a bare "measured" with no named origin: each is either the
    # dated live-run label or a source file in this repository.
    assert all(s.startswith(observed) or "app/ai/local_runtime.py" in s
               for s in sources.values()), sources
    for source in sources.values():
        assert HOME_ROOT not in source and TILDE_ROOT not in source, source


# ------------------------------------------------------------------- the seeder
def test_seeding_a_temporary_database_creates_everything_once(seeder, temp_state):
    tmp, data = temp_state
    plan = seeder.load_plan(CONTENT)
    first = seeder.seed_database(tmp / "wb.db", plan, data)
    assert first["created"] == {"project": 1, "folders": 4, "notes": 6, "questions": 5,
                                "decisions": 5, "drafts": 1, "table": 1}
    assert first["table"] == "table created"
    assert first["created_total"] == 23

    second = seeder.seed_database(tmp / "wb.db", plan, data)
    assert second["project_id"] == first["project_id"]
    assert second["created_total"] == 0
    assert set(second["created"].values()) == {0}
    assert second["table"] == "table already present"


def test_a_second_run_duplicates_no_row_in_any_table(seeder, temp_state):
    """Idempotency read from the database itself, not only from the report."""
    tmp, data = temp_state
    plan = seeder.load_plan(CONTENT)
    seeder.seed_database(tmp / "wb.db", plan, data)

    def census():
        h = harness_for(tmp / "wb.db", data)
        try:
            return {table: h.db.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                    for table in ("projects", "library_folders", "library_items",
                                  "questions", "decisions", "latex_drafts", "events")}
        finally:
            h.db.close()

    before = census()
    seeder.seed_database(tmp / "wb.db", plan, data)
    assert census() == before


def test_the_table_step_reports_itself_when_create_table_is_missing(seeder, temp_state, monkeypatch):
    """The one step this tool does not own: reported, never faked, never implemented here.

    ``library.create_table`` belongs to the numeric-check work. This seeder must
    call it only when it exists, so a build without it gets the exact skip line
    and an otherwise complete project.
    """
    tmp, data = temp_state
    monkeypatch.delattr(library, "create_table", raising=False)
    report = seeder.seed_database(tmp / "wb.db", seeder.load_plan(CONTENT), data)
    assert report["table"] == "table skipped: create_table not available yet"
    assert report["table"] == seeder.TABLE_SKIP
    assert report["created"]["table"] == 0
    assert report["created"]["notes"] == 6 and report["created"]["drafts"] == 1

    h = harness_for(tmp / "wb.db", data)
    try:
        project_id = h.db.execute("SELECT id FROM projects WHERE name=?", (PROJECT_NAME,)).fetchone()["id"]
        kinds = [item["kind"] for item in library.tree(h, project_id)["items"]]
    finally:
        h.db.close()
    assert library.TABLE_KIND not in kinds


def test_the_seeder_never_writes_the_table_itself():
    """The ownership rule, pinned: the writer stays in ``app.library``.

    The tool has a transport method per step, so a method named ``create_table``
    is expected; what must not appear is the tool writing a library row itself.
    Its only routes to a stored table are ``library.create_table`` in process and
    the one HTTP route the engine work adds.
    """
    source = TOOL.read_text(encoding="utf-8")
    assert "INSERT INTO" not in source
    assert "getattr(library, \"create_table\", None)" in source
    assert 'TABLE_SKIP = "table skipped: create_table not available yet"' in source
    # Exactly one route, with the body the work defined, and no second path tried:
    # the path literal appears once, as the constant, and nothing else in the tool
    # builds a table URL.
    assert 'TABLE_ROUTE = "/projects/{pid}/library/tables"' in source
    assert source.count('"/projects/{pid}/library/tables"') == 1
    assert '{"title": title, "csv_text": csv_text}' in source


def test_the_project_is_created_by_the_upload_mechanism_with_no_chip(seeder, temp_state):
    """How the project is created, and why: an upload, unbound to any chip.

    ``register_project`` only accepts a fixture kind from ``PROJECT_KINDS``, and
    none of them is this project. The right existing mechanism is
    ``register_upload``: the project's dataset is the measurements CSV with a
    dictionary that defines each column, which is exactly what an upload is. No
    pack is named, so the project belongs to no chip and stays openable from
    whichever chip is active; a demo project bound to one chip would be hidden
    behind that chip in the project list.
    """
    tmp, data = temp_state
    plan = seeder.load_plan(CONTENT)
    assert plan["project"]["pack_id"] is None
    seeder.seed_database(tmp / "wb.db", plan, data)
    h = harness_for(tmp / "wb.db", data)
    try:
        row = h.db.execute("SELECT id, kind, pack_id, files_json FROM projects WHERE name=?",
                           (PROJECT_NAME,)).fetchone()
        assert row["kind"].startswith("custom-"), row["kind"]
        assert row["pack_id"] is None
        # The dataset it registered is the measurements file, and the dictionary
        # defines one entry per column plus the coverage entry.
        files = json.loads(row["files_json"])
        assert len(files) == 2
        assert (data / files[0]).is_file()
        sources = json.loads((data / files[1]).read_text(encoding="utf-8"))
        assert {e["field"] for e in sources["entries"]} == {
            "metric", "value", "unit", "source", "(dataset coverage)"}
    finally:
        h.db.close()


def test_a_partially_seeded_project_is_completed_not_duplicated(seeder, temp_state):
    """An interrupted seed resumes: the missing notes are added, the rest is kept."""
    tmp, data = temp_state
    plan = seeder.load_plan(CONTENT)
    seeder.seed_database(tmp / "wb.db", plan, data)

    h = harness_for(tmp / "wb.db", data)
    try:
        project_id = h.db.execute("SELECT id FROM projects WHERE name=?", (PROJECT_NAME,)).fetchone()["id"]
        kept = library.tree(h, project_id)["items"]
        dropped = next(item for item in kept if item["title"] == FAQ_TITLE)
        assert dropped["kind"] == "note"
        h.db.execute("DELETE FROM library_items WHERE id=?", (dropped["id"],))
        h.db.commit()
    finally:
        h.db.close()

    report = seeder.seed_database(tmp / "wb.db", plan, data)
    assert report["project_id"] == project_id
    assert report["created"]["project"] == 0
    assert report["created"]["notes"] == 1
    assert report["created_total"] == 1
    h = harness_for(tmp / "wb.db", data)
    try:
        titles = [item["title"] for item in library.tree(h, project_id)["items"]
                  if item["kind"] == "note"]
    finally:
        h.db.close()
    assert len(titles) == len(plan["notes"]) == len(set(titles))


def test_the_cli_seeds_a_temporary_database_twice(seeder, tmp_path, capsys):
    _, data = tmp_path, tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    argv = ["--db", str(tmp_path / "cli.db"), "--data-dir", str(data)]
    assert seeder.main(argv) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["created_total"] == 23
    assert seeder.main(argv) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["created_total"] == 0
    assert second["project_id"] == first["project_id"]


def test_the_source_database_requires_an_explicit_opt_in(seeder):
    """``--db`` and ``--base-url`` exist; the source database is a named choice only."""
    assert seeder.source_database({"WB_DB": "/tmp/pinned.db"}) == Path("/tmp/pinned.db")
    assert seeder.source_database({}) == ROOT / "local_rehearsal.db"
    # The flag that seeds it is opt-in and mutually exclusive with the others.
    with pytest.raises(SystemExit):
        seeder.main(["--source-db", "--db", "/tmp/other.db"])


def test_the_opt_in_flag_seeds_only_the_database_wb_db_names(seeder, tmp_path, monkeypatch, capsys):
    """The source-app path is exercised through WB_DB, so no working database is touched.

    This is the only test that takes the ``--source-db`` branch, and it pins the
    environment to a temporary file first: the branch reads its target from
    ``WB_DB``, which is exactly what makes it safe to test.
    """
    data = tmp_path / "data"
    shutil.copytree(ROOT / "data", data)
    target = tmp_path / "opt-in.db"
    monkeypatch.setenv("WB_DB", str(target))
    assert seeder.main(["--source-db", "--data-dir", str(data)]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["created_total"] == 23
    assert target.is_file()
    assert (tmp_path / "local_rehearsal.db").exists() is False


# ------------------------------------------------------------------ over HTTP
@pytest.fixture()
def live_app(temp_state):
    """One loopback app on a temporary database: the seeder's HTTP transport.

    The app is the real one, built on this test's own temporary database and data
    copy, on an ephemeral loopback port. Its origin list is widened in place to
    that port exactly as the packaged supervisor does, so the seeder's Origin
    header is the one a real frontend would send and the app's policy still runs.
    """
    import uvicorn

    from app import api as api_module

    tmp, data = temp_state
    app = create_app(str(tmp / "http.db"), data)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0,
                                           log_level="warning", access_log=False))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started and thread.is_alive() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert server.started, "the loopback app did not start"
    port = server.servers[0].sockets[0].getsockname()[1]
    origin = f"http://127.0.0.1:{port}"
    saved_origins = list(api_module.ORIGINS)
    api_module.ORIGINS[:] = [origin]
    # The app object itself, so a test can install a route the engine work will
    # own and prove the seeder talks to it over real HTTP.
    LIVE_APPS[origin] = {"app": app, "db": str(tmp / "http.db"), "data": data, "extra": []}
    try:
        yield origin
    finally:
        for harness in LIVE_APPS.get(origin, {}).get("extra", []):
            harness.db.close()
        LIVE_APPS.pop(origin, None)
        api_module.ORIGINS[:] = saved_origins
        server.should_exit = True
        thread.join(timeout=30)


#: The app instance behind each live origin, so a test can add a route to it.
LIVE_APPS = {}


def remove_table_routes(origin):
    """Take the library-table routes OUT of a live app.

    They now ship in ``app/api.py``, so a test that wants the *skip* path (a build
    where the route has not landed) has to model that build explicitly. Removing
    the two route objects leaves every other route serving, and the client sees a
    405/404 exactly as an older build would answer.
    """
    entry = LIVE_APPS[origin]
    table_paths = {"/projects/{pid}/library/tables", "/library/tables/{iid}"}
    entry["app"].router.routes[:] = [
        route for route in entry["app"].router.routes
        if getattr(route, "path", None) not in table_paths]


def _get(url, origin):
    request = urllib.request.Request(url, headers={"Origin": origin})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=30) as response:
        return json.loads(response.read().decode())


def _post(url, payload, origin):
    """A write from the test itself, with the origin the app's policy accepts."""
    request = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST",
                                     headers={"Content-Type": "application/json",
                                              "Origin": origin})
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=30) as response:
        return json.loads(response.read().decode())


def test_seeding_a_running_app_over_http_is_idempotent(seeder, live_app):
    # A build without the table route: the step reports the skip instead of
    # pretending. (The app ships the route now, so that build is modelled.)
    remove_table_routes(live_app)
    plan = seeder.load_plan(CONTENT)
    first = seeder.seed_http(live_app, plan, origin=live_app)
    assert first["created"]["project"] == 1
    assert first["created"]["folders"] == 4
    assert first["created"]["notes"] == 6
    assert first["created"]["questions"] == 5
    assert first["created"]["decisions"] == 5
    assert first["created"]["drafts"] == 1
    assert first["table"] == seeder.TABLE_SKIP
    assert first["created"]["table"] == 0

    second = seeder.seed_http(live_app, plan, origin=live_app)
    assert second["created_total"] == 0
    assert second["project_id"] == first["project_id"]

    projects = _get(live_app + "/projects", live_app)["projects"]
    assert [p["name"] for p in projects].count(PROJECT_NAME) == 1


def test_the_seeder_posts_the_table_to_the_route_when_the_app_serves_it(seeder, live_app):
    """The app's own table route (``POST /projects/{pid}/library/tables`` in
    app/api.py) receives the table. A second run finds it and creates nothing.
    """
    plan = seeder.load_plan(CONTENT)

    first = seeder.seed_http(live_app, plan, origin=live_app)
    assert first["table"] == "table created"
    assert first["created"]["table"] == 1
    tree = _get(live_app + "/library/tree?project_id=" + first["project_id"], live_app)
    tables = [item for item in tree["items"] if item["kind"] == library.TABLE_KIND]
    assert [item["title"] for item in tables] == [plan["table"]["title"]]

    second = seeder.seed_http(live_app, plan, origin=live_app)
    assert second["created"]["table"] == 0
    assert second["table"] == "table already present"
    # Exactly one table row, read from the app the seeder actually talked to.
    assert len([i for i in _get(live_app + "/library/tree?project_id=" + first["project_id"],
                               live_app)["items"] if i["kind"] == library.TABLE_KIND]) == 1


def test_the_http_transport_names_the_apps_own_origin(seeder, live_app):
    """The header is the one the frontend sends, and it is what the app's policy reads.

    A foreign Origin is refused before any handler runs, so the seeder reports an
    unreachable app rather than leaving a half-written project behind.
    """
    # Without an explicit origin the transport sends the frontend's own, which
    # this app does not accept on its ephemeral port: the policy is really running.
    with pytest.raises(seeder.Unreachable):
        seeder.seed_http(live_app, seeder.load_plan(CONTENT))
    assert seeder.FRONTEND_ORIGIN == "http://localhost:5173"
    with pytest.raises(seeder.Unreachable):
        seeder.seed_http(live_app, seeder.load_plan(CONTENT), origin="https://evil.example")
    assert _get(live_app + "/projects", live_app)["projects"] == []


# ------------------------------------------------------------------ the package
def test_the_packaged_seed_creates_both_projects_in_a_fresh_state(packaged, live_app):
    report = packaged._seed(live_app)
    assert report["seeded"] is True
    assert len(report["projects"]) == 2, report
    assert report["created"]["project"] == 2
    assert report["created"]["folders"] == 4
    assert report["created"]["notes"] == 6
    assert report["created"]["questions"] == 5
    assert report["created"]["decisions"] == 5
    assert report["created"]["drafts"] == 1
    assert "error" not in report, report["error"]

    names = [p["name"] for p in _get(live_app + "/projects", live_app)["projects"]]
    assert names == ["FX-PERMIT-1 synthetic study", PROJECT_NAME]

    again = packaged._seed(live_app)
    assert again["seeded"] is False
    assert again["projects"] == report["projects"]


def test_a_session_that_holds_only_the_fx_sample_still_gets_the_dev_project(packaged, live_app):
    """A session is completed, not skipped, when part of it is already seeded.

    This is the case the reviewer reproduced: with only FX-PERMIT-1 present the
    seed used to return ``{"seeded": false}`` and create nothing. Each project is
    now read-before-write on its own name.
    """
    existing = _post(live_app + "/projects", {"kind": "fx"}, live_app)
    fx_id = existing.get("id") or existing.get("project_id")
    assert fx_id
    assert [p["name"] for p in _get(live_app + "/projects", live_app)["projects"]] == [
        "FX-PERMIT-1 synthetic study"]

    report = packaged._seed(live_app)
    assert report["seeded"] is True, report
    assert "error" not in report, report["error"]
    # The FX sample was found, not created again; the dev project was created.
    assert report["created"]["project"] == 1
    assert report["created"]["notes"] == 6
    assert report["created"]["drafts"] == 1
    assert report["projects"][0] == fx_id, "the existing FX sample is the one reported"

    names = [p["name"] for p in _get(live_app + "/projects", live_app)["projects"]]
    assert names == ["FX-PERMIT-1 synthetic study", PROJECT_NAME]

    # And a further call creates nothing at all.
    again = packaged._seed(live_app)
    assert again["seeded"] is False
    assert again["created"]["project"] == 0
    assert len(_get(live_app + "/projects", live_app)["projects"]) == 2


def test_a_dev_project_interrupted_after_creation_is_completed(packaged, live_app):
    """An interrupted seed resumes: created project, missing notes, completed on launch.

    The dev project exists with only one note, as if the previous launch stopped
    part way. The next launch must add the missing notes and the draft, keep the
    note that is already there, and create no second project.
    """
    plan = json.loads((CONTENT / "plan.json").read_text(encoding="utf-8"))
    csv_text = (CONTENT / plan["project"]["csv_file"]).read_text(encoding="utf-8")
    created = _post(live_app + "/projects/upload",
                    {"name": PROJECT_NAME, "csv_text": csv_text,
                     "dictionary": plan["project"]["dictionary"]}, live_app)
    project_id = created.get("project_id") or created.get("id")
    folder = _post(live_app + "/library/folders",
                   {"project_id": project_id, "name": plan["folders"][0]}, live_app)
    first_note = plan["notes"][0]
    _post(live_app + "/library/notes",
          {"project_id": project_id, "folder_id": folder["id"],
           "title": first_note["title"],
           "text": (CONTENT / first_note["file"]).read_text(encoding="utf-8")}, live_app)

    report = packaged._seed(live_app)
    assert "error" not in report, report["error"]
    assert report["created"]["project"] == 1, "only the FX sample is created"
    assert report["created"]["notes"] == 5, "the five missing notes, not six"
    assert report["created"]["folders"] == 3, "the three missing folders"
    assert report["created"]["drafts"] == 1

    names = [p["name"] for p in _get(live_app + "/projects", live_app)["projects"]]
    assert names.count(PROJECT_NAME) == 1, names
    tree = _get(live_app + "/library/tree?project_id=" + project_id, live_app)
    titles = sorted(i["title"] for i in tree["items"] if i["kind"] == "note")
    assert titles == sorted(note["title"] for note in plan["notes"])


def test_the_fx_sample_keeps_its_own_failure_handling(packaged, live_app, monkeypatch):
    """An FX failure is reported and the development project is still seeded.

    The synthetic sample is the one thing a review build must not lose, so its
    failure must not stop the other project, and it must not be retried in a loop.
    """
    real_call = packaged._json

    def flaky(url, payload=None, timeout=30):
        if url.endswith("/projects") and payload == {"kind": "fx"}:
            raise urllib.error.URLError("simulated fixture failure")
        return real_call(url, payload, timeout)

    monkeypatch.setattr(packaged, "_json", flaky)
    report = packaged._seed(live_app)
    assert "fx sample" in report["error"], report
    assert report["created"]["notes"] == 6, "the development project was still seeded"
    names = [p["name"] for p in _get(live_app + "/projects", live_app)["projects"]]
    assert names == [PROJECT_NAME]


def test_the_packaged_seed_reports_the_table_step_honestly(packaged, live_app):
    """A build without the route reports the skip, and writes no table to fake it."""
    remove_table_routes(live_app)
    report = packaged._seed(live_app)
    assert report["table"] == "table skipped: create_table not available yet"
    assert report["created"]["table"] == 0
    assert report["created"]["drafts"] == 1, "the draft is the one writing artifact the routes allow"
    # The table is not faked: the project lists no table item.
    projects = _get(live_app + "/projects", live_app)["projects"]
    project_id = next(p["id"] for p in projects if p["name"] == PROJECT_NAME)
    tree = _get(live_app + "/library/tree?project_id=" + project_id, live_app)
    assert [item for item in tree["items"] if item["kind"] == library.TABLE_KIND] == []


def test_the_packaged_seed_creates_the_table_when_the_route_exists(packaged, live_app):
    """A fresh review session gets the real results table, through the engine's route.

    The route is installed on the live app exactly as the engine work defines it,
    and the packaged seed then creates the table and reports it. A second launch
    in the same session finds the projects and creates nothing at all.
    """
    report = packaged._seed(live_app)
    assert report["table"] == "table created", report
    assert report["created"]["table"] == 1
    assert len(report["projects"]) == 2
    assert "error" not in report, report["error"]

    projects = _get(live_app + "/projects", live_app)["projects"]
    project_id = next(p["id"] for p in projects if p["name"] == PROJECT_NAME)
    tree = _get(live_app + "/library/tree?project_id=" + project_id, live_app)
    tables = [item for item in tree["items"] if item["kind"] == library.TABLE_KIND]
    assert [item["title"] for item in tables] == ["Measurements"]

    again = packaged._seed(live_app)
    assert again["seeded"] is False
    tree = _get(live_app + "/library/tree?project_id=" + project_id, live_app)
    assert len([i for i in tree["items"] if i["kind"] == library.TABLE_KIND]) == 1


def test_the_packaged_seed_reads_its_plan_from_the_bundle_layout(packaged):
    """The fixture directory is the app's own ``data/``, never a source-tree path."""
    assert packaged.FIXTURE_DATA == packaged.APP_ROOT / "data"
    assert packaged.FIXTURE_DATA == ROOT / "data"
    assert (packaged.FIXTURE_DATA / "dev_project" / "plan.json").is_file()


# ------------------------------------------------- the content is versioned source
def test_git_does_not_ignore_the_dev_project_and_still_ignores_the_rest_of_data():
    """The authored seed is source, so git does not ignore it; local data stays ignored.

    Run through ``git check-ignore`` against the repository root, which is what
    decides: the negation rule has to actually un-ignore the folder, and it has to
    leave the local data next to it ignored. Tracking itself is a later, separate
    step that needs the user's approval, so nothing is staged here.
    """
    import subprocess

    def check_ignore(rel_under_root):
        # Paths are relative to this run's cwd, which is the build root; git
        # resolves them inside the enclosing work tree.
        return subprocess.run(["git", "check-ignore", "-q", rel_under_root], cwd=ROOT,
                              capture_output=True).returncode == 0

    tracked = ROOT / "data" / "dev_project" / "plan.json"
    assert tracked.is_file()
    assert (ROOT.parent.parent / ".git").is_dir(), "the repository root is above this build"
    assert check_ignore("data/dev_project/plan.json") is False
    assert check_ignore("data/dev_project/notes/local_model.md") is False
    assert check_ignore("data/dev_project/measurements.csv") is False
    # Public authored fixtures ship; runtime library and installed chips do not.
    assert check_ignore("data/fx/sources.json") is False
    assert check_ignore("data/nyc/SNAPSHOT.json") is False
    for still_ignored in ("data/library", "data/chips"):
        assert check_ignore(still_ignored) is True, still_ignored


def test_the_review_source_builder_ships_the_dev_project_and_no_other_local_data():
    """The builder's own predicate, called: the seed is shipped, the rest is not."""
    builder = load(ROOT.parent.parent / "tools" / "review_source_package" / "build_review_source.py",
                   "build_review_source_under_test")
    try:
        assert builder.data_allowed("data/dev_project/plan.json") is True
        assert builder.data_allowed("data/dev_project/notes/local_model.md") is True
        assert builder.data_allowed("data/dev_project/measurements.csv") is True
        assert builder.data_allowed("data/fx/sources.json") is True
        assert builder.data_allowed("data/nyc/SNAPSHOT.json") is True
        assert builder.data_allowed("data/library/index.json") is False
        assert builder.data_allowed("data/chips/social_science.chip") is False
    finally:
        sys.modules.pop("build_review_source_under_test", None)
