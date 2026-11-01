"""Round-3 cases fixed in HARNESS_COMPARISON_PREREG_EN.md, revision 2."""
from dataclasses import replace

from tools.rehearse_fresh_comparison import CASES, GoldCase


def case(case_id, project, question, dataset, evidence, answer, index):
    sources = list(dict.fromkeys(e.split(":")[0] for e in evidence))
    return GoldCase(
        case_id=case_id, project_kind=project, question=question,
        expected_order=("basic", "harness") if index % 2 == 0 else ("harness", "basic"),
        dataset_ids=(dataset,), columns_absent=(), evidence_ids_present=tuple(evidence),
        frame={"sources": sources, "gap_type": "fact",
               "focus": "Read registered definitions and selected dataset columns"},
        result={"answer": answer, "claims": [{"text": answer, "evidence_ids": evidence,
                                             "kind": "fact"}],
                "unresolved": [], "proposed_variables": [],
                "next_action": "Researcher verifies source applicability before analysis."})


SPEC = [
    ("FX-STATUS-1", "fx", "What do A, I, W and X mean in status_code?",
     "fx/permits_2023.csv", ["DD-2023:status_code"],
     "A Approved, I Issued, W Withdrawn, X Expired."),
    ("NYC-APPROVED-1", "nyc", "Does DOB NOW approved_date mean that the permit was issued?",
     "nyc/dobnow_2024_subset.csv",
     ["DD-DOBNOW-2018:Column Info:Approved Date", "DD-DOBNOW-2018:Column Info:Issued Date"],
     "No. Entire job approved by the Plan Examiner; applicant can now pull a permit. "
     "Permit issuance is recorded separately."),
    ("NYC-EXPIRED-1", "nyc", "Does DOB NOW expired_date record issuance or expiry?",
     "nyc/dobnow_2024_subset.csv", ["DD-DOBNOW-2018:Column Info:Expired Date"],
     "Date the permit expires, not issuance."),
    ("NYC-SEQUENCE-1", "nyc", "What does sequence_number measure in DOB NOW?",
     "nyc/dobnow_2024_subset.csv", ["DD-DOBNOW-2018:Column Info:Sequence Number"],
     "Numeric order of permit issuance, not elapsed days or a calendar date."),
    ("BIS-FILING-1", "nyc", "What event does filing_date record in the selected BIS file?",
     "nyc/bis_2024_subset.csv", ["DD-BIS-2019:Column Info:Filing Date"],
     "Date the permit application was filed with DOB, not permit issuance."),
    ("BIS-RENEWAL-1", "nyc", "What distinction does filing_status encode in BIS?",
     "nyc/bis_2024_subset.csv", ["DD-BIS-2019:Column Info:Filing Status"],
     "First application versus renewal: INITIAL or RENEWAL, not issued/revoked permit status."),
    ("BIS-SEQUENCE-1", "nyc",
     "Does permit_sequence__ identify a job or successive permit issuances in BIS?",
     "nyc/bis_2024_subset.csv", ["DD-BIS-2019:Column Info:Permit Sequence #"],
     "Sequential number for each issuance of the particular permit: initial 01, "
     "subsequent renewals 02, 03, etc.; not a job identifier."),
]

ROUND3_CASES = tuple(replace(c, expected_order=("basic", "harness") if i % 2 == 0
                            else ("harness", "basic")) for i, c in enumerate(CASES)) + tuple(
    case(*spec, index=i + len(CASES)) for i, spec in enumerate(SPEC))
