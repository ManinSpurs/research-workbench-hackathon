"""Fresh-comparison fixture verification for the frozen gold cases in EVALUATION_EN.md.

Cases (frozen, EVALUATION_EN.md):
  FX-COST-1     "What does est_cost measure?"
                Gold: applicant-reported estimated cost in USD; not actual
                final cost; cite DD-2023:est_cost.
  FX-APPROVAL-1 "Does approval_date mean final approval?"
                Gold: plan-review completion in release 2023; OFF-NOTES-2025
                does not establish the 2023 meaning; final approval remains
                unestablished.
  NYC-DATE-1    "Can filing_date be used in DOB NOW?"
                Gold: check the selected DOB NOW file's actual columns;
                do not borrow BIS filing_date.

These tests FAIL LOUDLY (hard assertion with the missing artifact named)
when the repository fixtures cannot support a gold case. They never invent
data: a genuinely absent fixture fails with a "FIXTURE_MISSING: <name>"
message naming the fixture.

Registered IDs quoted below are read from the real fixture files:
  data/fx/sources.json  (DD-2023 definitions, OFF-FAQ-2023, OFF-NOTES-2025)
  data/nyc/sources.json (DD-BIS-2019, DD-DOBNOW-2018, META-BIS-COVERAGE)
Dataset fixtures:
  data/fx/permits_2023.csv
  data/nyc/dobnow_2024_subset.csv   (the selected DOB NOW file)
  data/nyc/bis_2024_subset.csv      (BIS contrast file for NYC-DATE-1)
"""
import csv
import json
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
FX_SOURCES = REPO / "data" / "fx" / "sources.json"
FX_PERMITS = REPO / "data" / "fx" / "permits_2023.csv"
NYC_SOURCES = REPO / "data" / "nyc" / "sources.json"
DOBNOW_CSV = REPO / "data" / "nyc" / "dobnow_2024_subset.csv"
BIS_CSV = REPO / "data" / "nyc" / "bis_2024_subset.csv"


def _require_file(path):
    assert path.is_file(), "FIXTURE_MISSING: %s" % path.relative_to(REPO)


def _load_json(path):
    _require_file(path)
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _header(path):
    _require_file(path)
    with open(path, encoding="utf-8", newline="") as fh:
        row = next(csv.reader(fh), None)
    assert row, "FIXTURE_MISSING: header row of %s" % path.relative_to(REPO)
    return [c.strip().strip('"') for c in row]


def _by_evidence(entries, evidence_id):
    for entry in entries:
        if entry.get("evidence_id") == evidence_id:
            return entry
    return None


# ---------------------------------------------------------------------------
# FX-COST-1: "What does est_cost measure?"
# ---------------------------------------------------------------------------

class TestFxCost1:
    def test_est_cost_column_present_in_permits_csv(self):
        cols = _header(FX_PERMITS)
        assert "est_cost" in cols, (
            "FX-COST-1 fixture failure: 'est_cost' column missing from "
            "data/fx/permits_2023.csv; actual columns=%s" % cols
        )

    def test_dd2023_est_cost_source_registered(self):
        fx = _load_json(FX_SOURCES)
        assert "DD-2023" in [e.get("source_id") for e in fx.get("dd2023", [])], (
            "FX-COST-1 fixture failure: source_id 'DD-2023' not registered "
            "in data/fx/sources.json dd2023 definitions"
        )
        entry = _by_evidence(fx.get("dd2023", []), "DD-2023:est_cost")
        assert entry is not None, (
            "FX-COST-1 fixture failure: evidence_id 'DD-2023:est_cost' "
            "missing from data/fx/sources.json"
        )
        assert entry.get("source_id") == "DD-2023"
        assert entry.get("field") == "est_cost"

    def test_dd2023_est_cost_definition_is_estimate_not_actual(self):
        fx = _load_json(FX_SOURCES)
        entry = _by_evidence(fx.get("dd2023", []), "DD-2023:est_cost")
        assert entry is not None, (
            "FX-COST-1 fixture failure: evidence_id 'DD-2023:est_cost' "
            "missing from data/fx/sources.json"
        )
        definition = entry.get("definition", "")
        assert definition == "Applicant-reported estimated cost in USD.", (
            "FX-COST-1 fixture failure: 'DD-2023:est_cost' definition changed; "
            "actual=%r" % definition
        )
        lowered = definition.lower()
        assert "actual" not in lowered and "final" not in lowered, (
            "FX-COST-1 fixture failure: 'DD-2023:est_cost' must define an "
            "estimate, not an actual/final cost; actual=%r" % definition
        )


# ---------------------------------------------------------------------------
# FX-APPROVAL-1: "Does approval_date mean final approval?"
# ---------------------------------------------------------------------------

class TestFxApproval1:
    def test_dd2023_approval_date_is_plan_review_completion(self):
        fx = _load_json(FX_SOURCES)
        entry = _by_evidence(fx.get("dd2023", []), "DD-2023:approval_date")
        assert entry is not None, (
            "FX-APPROVAL-1 fixture failure: evidence_id 'DD-2023:approval_date' "
            "missing from data/fx/sources.json"
        )
        assert "plan review" in entry.get("definition", "").lower(), (
            "FX-APPROVAL-1 fixture failure: 'DD-2023:approval_date' must define "
            "plan-review completion; actual=%r" % entry.get("definition")
        )
        assert "final approval" not in entry.get("definition", "").lower(), (
            "FX-APPROVAL-1 fixture failure: 'DD-2023:approval_date' must not "
            "claim final approval"
        )

    def test_release_2023_vs_off_notes_2025_distinction_representable(self):
        fx = _load_json(FX_SOURCES)
        faq = _by_evidence(fx.get("official", []), "OFF-FAQ-2023")
        notes = _by_evidence(fx.get("official", []), "OFF-NOTES-2025")
        assert faq is not None, (
            "FX-APPROVAL-1 fixture failure: evidence_id 'OFF-FAQ-2023' "
            "missing from data/fx/sources.json"
        )
        assert notes is not None, (
            "FX-APPROVAL-1 fixture failure: evidence_id 'OFF-NOTES-2025' "
            "missing from data/fx/sources.json"
        )
        # The 2023 release meaning is plan-review completion.
        assert "2023" in faq.get("notes", ""), (
            "FX-APPROVAL-1 fixture failure: 'OFF-FAQ-2023' lacks the release "
            "2023 applicability note; actual notes=%r" % faq.get("notes")
        )
        assert "plan review" in faq.get("definition", "").lower()
        # OFF-NOTES-2025 is scoped to the 2025 release only and therefore
        # cannot establish the 2023 meaning.
        assert "2025 only" in notes.get("notes", ""), (
            "FX-APPROVAL-1 fixture failure: 'OFF-NOTES-2025' lacks the "
            "'applies to release 2025 only' scope note; actual notes=%r"
            % notes.get("notes")
        )
        assert "2023" not in notes.get("notes", ""), (
            "FX-APPROVAL-1 fixture failure: 'OFF-NOTES-2025' must not claim "
            "2023 applicability; actual notes=%r" % notes.get("notes")
        )
        assert "final approval" in notes.get("definition", "").lower(), (
            "FX-APPROVAL-1 fixture failure: 'OFF-NOTES-2025' must carry the "
            "2025-only final-approval definition; actual=%r"
            % notes.get("definition")
        )

    def test_approval_date_column_present_in_permits_csv(self):
        cols = _header(FX_PERMITS)
        assert "approval_date" in cols, (
            "FX-APPROVAL-1 fixture failure: 'approval_date' column missing from "
            "data/fx/permits_2023.csv; actual columns=%s" % cols
        )


# ---------------------------------------------------------------------------
# NYC-DATE-1: "Can filing_date be used in DOB NOW?"
# The absence of filing_date in the DOB NOW fixture is the point: assert the
# fixture's actual column set explicitly instead of assuming it.
# ---------------------------------------------------------------------------

class TestNycDate1:
    EXPECTED_DOBNOW_COLUMNS = [
        "job_filing_number",
        "work_permit",
        "sequence_number",
        "filing_reason",
        "work_type",
        "borough",
        "approved_date",
        "issued_date",
        "expired_date",
        "permit_status",
    ]

    def test_dobnow_fixture_actual_column_set(self):
        cols = _header(DOBNOW_CSV)
        assert cols == self.EXPECTED_DOBNOW_COLUMNS, (
            "NYC-DATE-1 fixture failure: data/nyc/dobnow_2024_subset.csv "
            "column set changed; actual=%s" % cols
        )

    def test_dobnow_fixture_lacks_filing_date(self):
        cols = _header(DOBNOW_CSV)
        lowered = [c.lower() for c in cols]
        assert "filing_date" not in lowered, (
            "NYC-DATE-1 fixture failure: data/nyc/dobnow_2024_subset.csv now "
            "contains a filing_date column, invalidating the gold constraint "
            "'do not borrow BIS filing_date'; actual columns=%s" % cols
        )

    def test_bis_contrast_fixture_has_filing_date(self):
        cols = _header(BIS_CSV)
        assert "filing_date" in [c.lower() for c in cols], (
            "NYC-DATE-1 fixture failure: the BIS contrast file "
            "data/nyc/bis_2024_subset.csv lacks 'filing_date', so the "
            "do-not-borrow-BIS distinction is not demonstrable; "
            "actual columns=%s" % cols
        )

    def test_registered_dictionaries_match_column_distinction(self):
        nyc = _load_json(NYC_SOURCES)
        dobnow_fields = [e.get("field") for e in nyc.get("dobnow", [])]
        assert "DD-DOBNOW-2018" in {e.get("source_id") for e in nyc.get("dobnow", [])}, (
            "NYC-DATE-1 fixture failure: source_id 'DD-DOBNOW-2018' not "
            "registered in data/nyc/sources.json"
        )
        assert "Filing Date" not in dobnow_fields, (
            "NYC-DATE-1 fixture failure: registered 'DD-DOBNOW-2018' fields "
            "now include 'Filing Date', contradicting the gold constraint"
        )
        bis_entry = _by_evidence(
            nyc.get("bis", []), "DD-BIS-2019:Column Info:Filing Date"
        )
        assert bis_entry is not None, (
            "NYC-DATE-1 fixture failure: evidence_id "
            "'DD-BIS-2019:Column Info:Filing Date' missing from "
            "data/nyc/sources.json; the BIS-side filing_date definition "
            "needed for the contrast is not registered"
        )
