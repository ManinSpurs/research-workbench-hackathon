"""Deterministic numeric reconciliation: number reading and cell matching.

Pure function tests only: `reconcile` takes a claim string and a table
dictionary and answers with numbers, matches and a summary. No model, no file,
no database, and no network is involved, which the loopback-only fixture in
conftest.py enforces independently.
"""
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.numeric_check import reconcile


def table(columns, rows, title="Main results", item_id="li-1"):
    return {"item_id": item_id, "title": title, "columns": columns, "rows": rows}


def only(claim, table_dict):
    """The single number the claim states, for one assertion per case."""
    answer = reconcile(claim, table_dict)
    assert len(answer["numbers"]) == 1, answer["numbers"]
    return answer["numbers"][0]


PERCENT_TABLE = table(["Outcome", "Change (%)", "p-value", "Permits (n)", "Cost (USD)"],
                      [["Energy use", "5.2", "0.03", "1204", "1200000"],
                       ["Water use", "5.9", "0.11", "980", "900000"],
                       ["Waste", "-0.3", "0.4", "300", "45000"]])


# ------------------------------------------------------------------ contract
def test_answer_shape_and_summary_add_up():
    answer = reconcile("The policy cut energy use by 5.2% (p < 0.05) across 1,204 permits",
                       PERCENT_TABLE)
    assert set(answer) == {"numbers", "summary"}
    assert answer["summary"] == {"total": 3, "match": 3, "near_mismatch": 0, "not_found": 0}
    for number in answer["numbers"]:
        assert set(number) == {"text", "start", "end", "value", "kind",
                               "matches", "status", "nearest", "reason"}
        assert isinstance(number["text"], str) and isinstance(number["value"], float)
        assert isinstance(number["start"], int) and isinstance(number["end"], int)
        assert number["kind"] in {"percent", "count", "decimal", "p_value", "coefficient",
                                  "money", "other"}
        assert number["status"] in {"match", "near_mismatch", "not_found"}
        for match in number["matches"]:
            assert set(match) == {"row", "col", "column", "cell", "value",
                                  "column_scale", "scale_source"}
            assert isinstance(match["value"], float)
    matched = [n for n in answer["numbers"] if n["status"] == "match"]
    assert len(matched) == answer["summary"]["match"]
    assert answer["summary"]["total"] == len(answer["numbers"])


def test_spans_point_at_the_number_as_written():
    claim = "Energy use fell 5.2% (p < 0.05) across 1,204 permits"
    answer = reconcile(claim, PERCENT_TABLE)
    assert [n["text"] for n in answer["numbers"]] == ["5.2%", "p < 0.05", "1,204"]
    for number in answer["numbers"]:
        assert claim[number["start"]:number["end"]] == number["text"]
    assert answer["numbers"] == reconcile(claim, PERCENT_TABLE)["numbers"], "deterministic"


def test_inputs_are_not_modified():
    before = {"columns": list(PERCENT_TABLE["columns"]),
              "rows": [list(row) for row in PERCENT_TABLE["rows"]]}
    reconcile("5.2% and 1,204 permits", PERCENT_TABLE)
    assert PERCENT_TABLE["columns"] == before["columns"]
    assert PERCENT_TABLE["rows"] == before["rows"]


# ------------------------------------------------------------------ number forms
@pytest.mark.parametrize("claim, kind, value", [
    ("1204 permits", "count", 1204.0),
    ("1,204 permits", "count", 1204.0),
    ("1 204 permits", "count", 1204.0),
    ("4.75 points", "decimal", 4.75),
    ("5.2%", "percent", 5.2),
    ("5.2 percent", "percent", 5.2),
    ("5.2 퍼센트", "percent", 5.2),
    ("5.2퍼센트 감소", "percent", 5.2),
    ("5.2%p", "other", 5.2),
    ("5.2 percentage points", "other", 5.2),
    ("p < 0.05", "p_value", 0.05),
    ("p = .03", "p_value", 0.03),
    ("p\u22640.05", "p_value", 0.05),
    ("$1.2M", "money", 1200000.0),
    ("1.2 million", "decimal", 1200000.0),
    ("1.2 billion dollars", "money", 1200000000.0),
    ("1.2e-3", "decimal", 0.0012),
    ("-0.3", "coefficient", -0.3),
    ("\u22120.3", "coefficient", -0.3),
    ("+5.2%", "percent", 5.2),
    ("-$1.2M", "money", -1200000.0),
])
def test_each_written_number_is_recognised(claim, kind, value):
    number = only(claim, PERCENT_TABLE)
    assert (number["kind"], number["value"]) == (kind, value)


def test_thousands_separators_with_both_writings_agree():
    claim = "1,204 permits"

    def permits(cell):
        return only(claim, table(["Permits (n)"], [[cell]]))["status"]

    assert permits("1204") == "match"
    assert permits("1,204") == "match"
    assert permits("1 204") == "match"
    assert permits("1204.0") == "match", "a stored decimal is the same count"


def test_range_is_read_as_two_numbers():
    answer = reconcile("The effect was between 3\u20135% of households", PERCENT_TABLE)
    assert [(n["text"], n["kind"], n["value"]) for n in answer["numbers"]] == [
        ("3", "percent", 3.0), ("5%", "percent", 5.0)]
    assert answer["summary"]["total"] == 2
    hyphen = reconcile("a 3-5% range and a 10\u201320 percentage point range", PERCENT_TABLE)
    assert [(n["kind"], n["value"]) for n in hyphen["numbers"]] == [
        ("percent", 3.0), ("percent", 5.0), ("other", 10.0), ("other", 20.0)]


def test_money_and_percent_are_different_kinds_of_number():
    assert only("$1.2M", PERCENT_TABLE)["kind"] == "money"
    assert only("5.2%", PERCENT_TABLE)["kind"] == "percent"
    assert only("5.2", PERCENT_TABLE)["kind"] == "decimal"


# ------------------------------------------------------------------ what is ignored
@pytest.mark.parametrize("claim", [
    "In 2024 the city issued permits.",
    "Between 1900 and 2100 the trend reverses.",
    "The 2019 study agrees [12] and so does this one [3, 4].",
    "Table 2 and Figure 3 report it, and section 4 says the same.",
    "See footnote 4 for the estimate.",
    "It placed 2nd in the ranking.",
    "Version 1.9.1 shipped.",
    "Data from 2020-03-15 are missing.",
    "Item 7 on line 3 of column 2.",
])
def test_numbers_that_are_not_claims_are_ignored(claim):
    answer = reconcile(claim, PERCENT_TABLE)
    assert answer["numbers"] == []
    assert answer["summary"] == {"total": 0, "match": 0, "near_mismatch": 0, "not_found": 0}


def test_a_year_cell_is_still_data_but_a_year_claim_is_a_date():
    dated = table(["Outcome", "Year"], [["Energy use", "2024"]])
    assert reconcile("In 2024 the policy cut use", dated)["numbers"] == []
    # The cell itself is a value in its column, so a claim about it still matches.
    claim = "the study covers 2,024 permits"
    assert reconcile(claim, table(["Year"], [["2024"]]))["numbers"][0]["status"] == "match"


# ------------------------------------------------------------------ precision
def test_claim_precision_sets_the_matching_window():
    window = table(["Change (%)"], [["5.15"], ["5.25"], ["5.2"]])
    answer = reconcile("a change of 5.2%", window)
    number = answer["numbers"][0]
    assert number["status"] == "match"
    assert [m["cell"] for m in number["matches"]] == ["5.15", "5.2"], "5.15 is in, 5.25 is out"


def test_a_cell_beyond_the_window_but_one_written_step_away_differs_by_rounding():
    # 0.031 is 55 percent off 0.02, so it is not a relative near miss; rounded to
    # the claim's two written places it becomes 0.03, one step from the claim.
    number = only("the difference was 0.02", table(["Change"], [["0.031"]]))
    assert number["status"] == "near_mismatch"
    assert number["nearest"]["cell"] == "0.031"
    assert number["nearest"]["reason"] == "differs only by rounding direction"


def test_a_more_precise_cell_inside_the_window_is_a_match():
    # The claim's own precision decides: 5.234 lies in [5.15, 5.25).
    assert only("5.2%", table(["Change (%)"], [["5.234"]]))["status"] == "match"
    assert only("5.2%", table(["Change (%)"], [["5.25"]]))["status"] != "match"


def test_percent_matches_the_equivalent_fraction_both_ways():
    fraction_cell = table(["Change (fraction)"], [["0.052"]])
    assert only("a fall of 5.2%", fraction_cell)["matches"][0]["value"] == 0.052

    percent_cell = table(["Change (%)"], [["5.2"]])
    as_fraction = only("a fall of 0.052", percent_cell)
    assert as_fraction["status"] == "match"
    assert as_fraction["matches"][0]["value"] == 5.2


def test_a_count_matches_only_an_equal_value():
    number = only("1,204 permits", table(["Permits (n)"], [["1204"]]))
    assert number["status"] == "match"
    off = only("1,204 permits", table(["Permits (n)"], [["1210"]]))
    assert off["status"] == "near_mismatch"
    assert off["nearest"]["difference"] == 6.0


def test_p_value_claim_matches_a_cell_below_the_threshold():
    number = only("p < 0.05", PERCENT_TABLE)
    assert number["status"] == "match"
    assert [m["cell"] for m in number["matches"]] == ["0.03"]
    assert [m["column"] for m in number["matches"]] == ["p-value"]


def test_p_value_claim_with_no_cell_below_the_threshold_is_not_found():
    higher = table(["Outcome", "p-value"], [["Energy", "0.11"], ["Water", "0.4"]])
    number = only("p < 0.05", higher)
    assert number["status"] == "not_found"
    assert number["matches"] == [] and number["nearest"] is None


def test_p_value_claim_needs_a_p_like_column():
    not_a_p_column = table(["Outcome", "Change (%)"], [["Energy", "0.03"]])
    assert only("p < 0.05", not_a_p_column)["status"] == "not_found"


def test_a_percent_claim_is_never_near_a_p_value_column():
    """The exact regression: "5.2%" was near-missed against a p column's 0.05.

    The p-value rule is symmetric, so a p-like column compares only with
    p-value claims and a p-value claim only with p-like columns — neither
    matching nor reporting a near miss across the two.
    """
    number = only("5.2%", table(["p"], [["0.05"]]))
    assert number["status"] == "not_found"
    assert number["matches"] == [] and number["nearest"] is None


@pytest.mark.parametrize("claim", ["5.2%", "0.05", "5 permits", "12"])
def test_no_claim_kind_is_near_a_p_like_column(claim):
    for header in ("p", "p-value", "p_value", "sig", "significance", "\uc720\uc758\ud655\ub960"):
        number = only(claim, table([header], [["0.05"]]))
        assert number["nearest"] is None, (claim, header)
        assert number["matches"] == [], (claim, header)


def test_p_value_equality_matches_the_stated_value():
    assert only("p = 0.03", PERCENT_TABLE)["status"] == "match"
    assert only("p = .03", PERCENT_TABLE)["status"] == "match"
    assert only("p = 0.04", PERCENT_TABLE)["status"] == "near_mismatch"


def test_sign_matters_and_the_reason_is_recorded():
    positive_claim = only("waste fell 0.3%", PERCENT_TABLE)
    assert positive_claim["status"] == "near_mismatch"
    assert positive_claim["matches"] == []
    assert positive_claim["nearest"]["cell"] == "-0.3"
    assert positive_claim["nearest"]["difference"] == pytest.approx(0.6)
    assert "sign differs" in positive_claim["nearest"]["reason"]
    assert only("-0.3%", PERCENT_TABLE)["status"] == "match"


# ------------------------------------------------------------------ near misses
def test_near_mismatch_reports_the_nearest_cell_and_the_difference():
    number = only("a fall of 6.1%", PERCENT_TABLE)
    assert number["status"] == "near_mismatch"
    assert number["matches"] == []
    assert number["nearest"]["cell"] == "5.9"
    assert number["nearest"]["column"] == "Change (%)"
    assert number["nearest"]["difference"] == pytest.approx(0.2)
    assert number["nearest"]["reason"] == "within 25% relative difference"


def test_a_cell_beyond_25_percent_is_not_a_near_miss():
    number = only("a fall of 5.2%", table(["Change (%)"], [["9.9"]]))
    assert number["status"] == "not_found"
    assert number["nearest"] is None


def test_a_cell_of_another_kind_is_never_the_nearest():
    # 5.9 percent is near, but the p-value column is a different family.
    number = only("p = 0.05", table(["Outcome", "p-value", "Change (%)"],
                                   [["Energy", "0.11", "5.1"]]))
    assert number["nearest"] is None


def test_nothing_in_common_is_not_found():
    answer = reconcile("a total of 99,999 units", PERCENT_TABLE)
    assert answer["numbers"][0]["status"] == "not_found"
    assert answer["summary"] == {"total": 1, "match": 0, "near_mismatch": 0, "not_found": 1}


# ------------------------------------------------------------------ ordering
def test_header_similarity_orders_the_matches_before_row_order():
    two_columns = table(["Zeta (%)", "Energy change (%)"],
                        [["5.2", "5.2"], ["5.2", "5.2"]])
    answer = reconcile("the energy change was 5.2%", two_columns)
    matches = answer["numbers"][0]["matches"]
    assert [(m["row"], m["column"]) for m in matches] == [
        (0, "Energy change (%)"), (1, "Energy change (%)"), (0, "Zeta (%)"), (1, "Zeta (%)")]


def test_row_then_column_breaks_a_tie():
    answer = reconcile("5.2%", table(["First (%)", "Second (%)"],
                                     [["5.2", "5.2"], ["5.2", "5.2"]]))
    assert [(m["row"], m["col"]) for m in answer["numbers"][0]["matches"]] == [
        (0, 0), (0, 1), (1, 0), (1, 1)]


def test_the_same_input_always_names_the_same_cell():
    tied = table(["Change (%)", "Other (%)"], [["6.0", "6.0"]])
    first = only("a fall of 5.9%", tied)
    second = only("a fall of 5.9%", tied)
    assert first == second
    assert first["nearest"]["col"] == 0


# ------------------------------------------------------------------ table handling
def test_non_numeric_cells_are_skipped():
    sparse = table(["Outcome", "Change (%)"],
                   [["Energy", "n/a"], ["Water", ""], ["Waste", "-"], ["Air", "5.2"]])
    number = only("a fall of 5.2%", sparse)
    assert number["status"] == "match"
    assert number["matches"][0]["row"] == 3
    assert only("a fall of 5.2%", table(["Outcome", "Change (%)"], [["Energy", "n/a"]]))[
        "status"] == "not_found"


def test_an_empty_table_answers_not_found_without_raising():
    empty = table([], [])
    answer = reconcile("5.2% across 1,204 permits", empty)
    assert answer["summary"] == {"total": 2, "match": 0, "near_mismatch": 0, "not_found": 2}
    assert [n["nearest"] for n in answer["numbers"]] == [None, None]


def test_short_rows_are_read_as_missing_cells():
    ragged = table(["Outcome", "Change (%)"], [["Energy"], ["Water", "5.2"]])
    assert only("5.2%", ragged)["matches"][0]["row"] == 1


def test_a_repeated_value_in_a_large_table_is_bounded():
    wide = table(["Change (%)"], [["5.2"] for _ in range(400)])
    matches = reconcile("5.2%", wide)["numbers"][0]["matches"]
    assert len(matches) == 200, "the answer is capped so a large table cannot flood it"
    assert [m["row"] for m in matches[:3]] == [0, 1, 2]


# ------------------------------------------------------------------ Korean and English
def test_korean_claim_is_read_and_matched():
    korean = table(["항목", "변화율 (%)", "p값", "허가 건수"],
                   [["에너지", "5.2", "0.03", "1204"],
                    ["용수", "5.9", "0.11", "980"]])
    answer = reconcile("정책으로 에너지 사용량이 5.2퍼센트 줄었고 p < 0.05, 허가 1,204건이었다", korean)
    assert [n["kind"] for n in answer["numbers"]] == ["percent", "p_value", "count"]
    assert [n["status"] for n in answer["numbers"]] == ["match", "match", "match"]
    assert answer["summary"] == {"total": 3, "match": 3, "near_mismatch": 0, "not_found": 0}


def test_english_claim_names_the_row_it_does_not_match():
    answer = reconcile("The policy cut water use by 4.5% across 1,204 permits", PERCENT_TABLE)
    percent, count = answer["numbers"]
    assert percent["status"] == "near_mismatch"
    assert percent["nearest"]["cell"] == "5.2"
    assert percent["nearest"]["row"] == 0
    assert count["status"] == "match"
    far = reconcile("The policy cut water use by 4.1%", PERCENT_TABLE)["numbers"][0]
    assert far["status"] == "not_found", "1.1 points off 5.2 is more than 25 percent"


def test_a_claim_far_from_every_cell_is_not_found():
    assert only("a fall of 6.5%", PERCENT_TABLE)["status"] == "near_mismatch"
    assert only("a fall of 30%", PERCENT_TABLE)["status"] == "not_found"


# ------------------------------------------------------------------ adversarial input
def test_a_very_large_table_stays_fast():
    """2,000 rows of 50 columns is the stored-table ceiling: 100,000 cells."""
    columns = ["Outcome", "change (%)", "Permits (n)"] + [f"col_{index}" for index in range(47)]
    rows = [["r0", "5.2", "1204"] + [str((7 + column) % 1000) for column in range(47)]]
    for row in range(1, 2000):
        rows.append([f"r{row}", str(round(1 + row / 400, 2)), str(1000 + row)]
                    + [str((row * 7 + column) % 1000) for column in range(47)])
    started = time.monotonic()
    answer = reconcile("The policy cut energy use by 5.2% (p < 0.05) across 1,204 permits",
                       table(columns, rows))
    elapsed = time.monotonic() - started
    assert elapsed < 20, f"100,000 cells took {elapsed:.1f}s"
    assert answer["summary"]["total"] == 3
    assert answer["numbers"][0]["status"] == "match"
    assert answer["numbers"][0]["matches"][0]["cell"] == "5.2"
    assert answer["numbers"][2]["status"] == "match"
    assert answer["numbers"][2]["matches"][0]["cell"] == "1204"
    assert answer["numbers"][1]["status"] == "not_found", "no p-like column exists"


def test_unusual_text_never_raises():
    odd = table(["a,b", "€", "", "p.v."], [["]],", "1,2", "5.2", ".03"]])
    for claim in ("", "!!!", ",,,", "-", "p", "1e999", "\u2212", "1,2,3", "....", "5.2%%"):
        answer = reconcile(claim, odd)
        assert set(answer) == {"numbers", "summary"}
    assert reconcile("-", table(["x"], [["-"]]))["numbers"] == []


def test_a_malformed_thousands_group_is_not_read_as_two_numbers():
    assert reconcile("code 12,04 here", PERCENT_TABLE)["numbers"] == []


def test_the_same_number_written_twice_is_reported_twice():
    answer = reconcile("5.2% now and 5.2% again", PERCENT_TABLE)
    assert answer["summary"]["total"] == 2
    assert answer["summary"]["match"] == 2
    assert answer["numbers"][0]["start"] != answer["numbers"][1]["start"]


def test_claim_text_must_be_text_and_table_must_be_an_object():
    with pytest.raises(ValueError):
        reconcile(None, PERCENT_TABLE)
    with pytest.raises(ValueError):
        reconcile("5.2%", None)
    with pytest.raises(ValueError):
        reconcile("5.2%", {"columns": "not a list", "rows": []})


# ------------------------------------------------------------------ regressions
# Each test below pins one defect found in independent review, with the exact
# input that failed. A false match is the worst outcome here: a researcher acts
# on "match", so these assert the absence of a match as strongly as its presence.
def test_a_dash_after_a_unit_is_a_range_separator_not_a_minus():
    """"3%–5%" is two positive percent bounds, never 3 and -5.

    The table holds -5 in a percent column: reading the second bound as a minus
    made that an exact match, which would have told a researcher the policy cut
    energy use when the claim says the opposite.
    """
    table_dict = table(["Change (%)"], [["-5"]])
    for claim in ("3%\u20135%", "3%-5%"):
        numbers = reconcile(claim, table_dict)["numbers"]
        assert [n["text"] for n in numbers] == ["3%", "5%"], claim
        assert [n["value"] for n in numbers] == [3.0, 5.0], claim
        assert all(n["kind"] == "percent" for n in numbers), claim
        assert numbers[1]["status"] != "match", "-5 must not answer a positive 5% bound"
        assert numbers[1]["matches"] == [], claim


def test_both_bounds_of_a_unicode_dash_range_are_percent_numbers():
    numbers = reconcile("5.2%\u20136.0%", table(["Change (%)"], [["-6.0"]]))["numbers"]
    assert [n["value"] for n in numbers] == [5.2, 6.0]
    assert [n["kind"] for n in numbers] == ["percent", "percent"]
    assert numbers[1]["matches"] == [], "a -6.0 cell must not match a 6.0 bound"


def test_a_money_range_keeps_both_bounds():
    """"$1.2M–$1.5M" is two money numbers; the second used to be dropped."""
    answer = reconcile("$1.2M\u2013$1.5M", table(["Cost (USD)"], [["1500000"]]))
    assert answer["summary"]["total"] == 2
    assert [n["value"] for n in answer["numbers"]] == [1200000.0, 1500000.0]
    assert [n["kind"] for n in answer["numbers"]] == ["money", "money"]
    assert answer["numbers"][1]["status"] == "match"
    assert answer["numbers"][0]["status"] != "match"


def test_a_unit_on_the_first_bound_governs_the_second():
    numbers = reconcile("5%-6", table(["Change (%)"], [["-6"]]))["numbers"]
    assert [n["value"] for n in numbers] == [5.0, 6.0]
    assert [n["kind"] for n in numbers] == ["percent", "percent"]
    assert numbers[1]["matches"] == [], "a -6 cell must not match a 6 percent bound"


def test_a_genuine_leading_minus_survives():
    """A dash after a word is still a sign, not a range separator."""
    table_dict = table(["Change (%)"], [["-5"]])
    for claim in ("fell to -5%", "from 3% to -5%"):
        numbers = reconcile(claim, table_dict)["numbers"]
        negative = [n for n in numbers if n["value"] == -5.0]
        assert len(negative) == 1, claim
        assert negative[0]["kind"] == "percent", claim
        assert negative[0]["status"] == "match", claim
    assert reconcile("from 3% to -5%", table_dict)["numbers"][0]["value"] == 3.0


def test_a_percent_claim_never_matches_a_plain_cell_of_the_same_digits():
    """0.3 in a fraction column is thirty percent, a hundred times "0.3%"."""
    for column in ("Change (fraction)", "Change"):
        number = only("0.3%", table([column], [["0.3"]]))
        assert number["status"] != "match", column
        assert number["matches"] == [], column


def test_the_percent_scale_confusion_is_reported_as_a_near_miss():
    number = only("0.3%", table(["Change (fraction)"], [["0.3"]]))
    assert number["status"] == "near_mismatch"
    assert number["nearest"]["cell"] == "0.3"
    assert number["nearest"]["reason"] == "scale differs (percent vs fraction)"


def test_a_percent_claim_still_matches_a_percent_cell_and_a_fraction_cell():
    """The two genuine equivalences survive the stricter rule."""
    assert only("5.2%", table(["Change (%)"], [["5.2"]]))["status"] == "match"
    assert only("5.2%", table(["Change (fraction)"], [["0.052"]]))["status"] == "match"
    assert only("0.052", table(["Change (%)"], [["5.2"]]))["status"] == "match"
    # A percent cell holding the bare digits is a *different* quantity (0.5%),
    # so 50% must not reach it.
    assert only("50%", table(["Change (%)"], [["0.5"]]))["status"] != "match"


def test_a_claim_without_a_unit_still_matches_a_plain_cell():
    assert only("0.3", table(["Change (fraction)"], [["0.3"]]))["status"] == "match"
    assert only("1204", table(["Permits"], [["1204"]]))["status"] == "match"


@pytest.mark.parametrize("claim", [
    "Tables 2 and 3",
    "Tables 2-3",
    "Figures 4 and 5",
    "Sections 4 and 5",
    "Appendix 2 and 3",
    "No. 4",
    "p. 12",
    "pp. 12-14",
    "(12)",
    "\ud45c 3",
    "\uadf8\ub9bc 3",
    "\ubd80\ub85d 2",
    "\uc808 4",
    "\ucabd 12",
    "\uc81c3\uc7a5",
])
def test_label_and_reference_numbers_are_not_claims(claim):
    """Not one number of a label, a page reference or a citation is a claim."""
    answer = reconcile(claim, table(["n"], [["1"], ["2"], ["3"], ["4"], ["5"], ["12"]]))
    assert answer["numbers"] == [], claim
    assert answer["summary"]["total"] == 0, claim


def test_every_member_of_a_label_list_is_masked():
    """A list masks through its last member, not only its first."""
    answer = reconcile("see Tables 2, 3 and 4 plus Figures 5-7",
                       table(["n"], [["2"], ["3"], ["4"], ["5"], ["6"], ["7"]]))
    assert answer["numbers"] == []


def test_a_real_quantity_list_is_still_read():
    """Masking a label must not swallow a claim's own figures."""
    numbers = reconcile("5.2% across 1,204 permits and 3 sites",
                        table(["Change (%)"], [["5.2"]]))["numbers"]
    assert [n["value"] for n in numbers] == [5.2, 1204.0, 3.0]


@pytest.mark.parametrize("claim", ["p\uac12 < 0.05", "\uc720\uc758\ud655\ub960 < 0.05"])
def test_korean_p_values_are_p_values(claim):
    p_table = table(["Outcome", "p\uac12"], [["energy", "0.03"]])
    number = only(claim, p_table)
    assert number["kind"] == "p_value"
    assert number["value"] == 0.05
    assert number["status"] == "match"


def test_a_korean_p_value_never_matches_a_percent_column():
    number = only("p\uac12 < 0.05", table(["Change (%)"], [["0.03"]]))
    assert number["kind"] == "p_value"
    assert number["status"] == "not_found"
    assert number["matches"] == []


def test_a_korean_p_value_reads_the_korean_significance_header():
    number = only("\uc720\uc758\ud655\ub960 < 0.05", table(["\uc720\uc758\ud655\ub960"], [["0.2"]]))
    assert number["status"] == "not_found", "0.2 is not below 0.05"


@pytest.mark.parametrize("claim, kind, value", [
    ("5k simulations", "count", 5000.0),
    ("5K respondents", "count", 5000.0),
    ("5 thousand firms", "count", 5000.0),
    ("1.2 million", "decimal", 1200000.0),
    ("1.2M", "decimal", 1200000.0),
    ("1.2bn", "decimal", 1200000000.0),
])
def test_a_multiplier_without_a_currency_is_not_money(claim, kind, value):
    """A letter multiplier scales whether or not a currency is written; a
    lowercase "m" is settled separately, because it may be metres or millions."""
    number = only(claim, table(["n"], [["5"]]))
    assert number["kind"] == kind, claim
    assert number["value"] == value, claim
    assert number["kind"] != "money", claim


def test_a_letter_multiplier_scales_the_count_it_qualifies():
    assert only("5K respondents", table(["Respondents"], [["5000"]]))["status"] == "match"
    assert only("5k simulations", table(["Simulations"], [["5000"]]))["status"] == "match"


def test_a_multiplier_with_a_currency_is_money():
    assert only("$1.2M", table(["Cost"], [["1200000"]]))["kind"] == "money"
    assert only("1.2 million dollars", table(["Cost"], [["1200000"]]))["kind"] == "money"
    assert only("\u20a91.2M", table(["Cost"], [["1200000"]]))["kind"] == "money"
    assert only("1.2M USD", table(["Cost"], [["1200000"]]))["kind"] == "money"


def test_five_metres_does_not_become_five_million():
    """A bare "5m" beside a spatial word is ambiguous, so it matches nothing."""
    number = only("the site is 5m wide", table(["Width"], [["5"]]))
    assert number["value"] is None
    assert number["status"] == "not_found"
    assert number["matches"] == [] and number["nearest"] is None


@pytest.mark.parametrize("cell", ["-.5", "-.03", "-.5e-3", "-$1.2M", "-0.3 %", "+5.2 %"])
def test_a_legitimate_negative_is_not_a_formula(cell):
    """The formula guard accepts exactly what the number reader parses."""
    from app.numeric_check import looks_like_formula

    assert looks_like_formula(cell) is False, cell
    assert reconcile(f"the value was {cell}", table(["n"], [["1"]]))["summary"]["total"] == 1


@pytest.mark.parametrize("cell", [
    "=SUM(A1:A9)", "+A1", "-A1+B2", "@cmd", "=1+2", "\t=1+2", "\r@cmd",
])
def test_a_real_formula_is_still_refused(cell):
    from app.numeric_check import looks_like_formula

    assert looks_like_formula(cell) is True, cell


def test_a_lone_dash_is_a_no_data_marker_not_a_formula():
    from app.numeric_check import looks_like_formula

    assert looks_like_formula("-") is False
    assert looks_like_formula(" -- ") is False


@pytest.mark.parametrize("claim", ["1e999", "1e2147483648", "1e-2147483648", "1.2e400"])
def test_a_non_finite_number_is_reported_not_raised(claim):
    """No input may raise, and every value must survive json.dumps."""
    import json

    answer = reconcile(claim, table(["n"], [["1"]]))
    assert answer["numbers"][0]["value"] is None
    assert answer["numbers"][0]["status"] == "not_found"
    assert answer["numbers"][0]["matches"] == []
    json.dumps(answer)  # must not raise, and no Infinity token is written


def test_a_huge_cell_value_never_matches_anything():
    answer = reconcile("5 permits", table(["n"], [["1e999"]]))
    assert answer["numbers"][0]["matches"] == []
    assert answer["numbers"][0]["status"] == "not_found"


def test_a_thousands_separated_year_stays_a_count():
    """"In 2,024 the policy was adopted" keeps 2,024 — a documented choice."""
    number = only("In 2,024 the policy was adopted", table(["n"], [["2024"]]))
    assert number["value"] == 2024.0
    assert number["kind"] == "count"
    assert number["status"] == "match"


def test_a_bare_year_is_still_ignored():
    assert reconcile("In 2024 the policy was adopted", table(["n"], [["2024"]]))["numbers"] == []


@pytest.mark.parametrize("claim", [
    "Table 2, 5.2% of the total",
    "Section 4 and 5.2% of cases",
    "Table 2 shows 5.2% across 1,204 permits",
    "Figure 1, 5.9% of permits",
])
def test_a_label_next_to_a_real_figure_does_not_swallow_the_figure(claim):
    """Masking a label must stop at a figure that is not a reference.

    A blanket list-continuation read "Table 2, 5.2%" as a two-member list and
    dropped the claim's own number — a false negative, and the mirror image of
    the leak this masking exists to prevent.
    """
    numbers = [n for n in reconcile(claim, PERCENT_TABLE)["numbers"]]
    assert numbers, claim
    assert all(n["value"] is not None for n in numbers), claim
    assert any(n["kind"] == "percent" for n in numbers), claim


def test_a_masked_list_stops_at_the_end_of_the_list():
    answer = reconcile("Tables 2, 3 and 4 show a 5.2% fall", PERCENT_TABLE)
    assert [n["value"] for n in answer["numbers"]] == [5.2]


# ------------------------------------------------- column scale (regression R1)
@pytest.mark.parametrize("column, cell, difference", [
    ("energy_change", "5.2", 0.51),
    ("inflation", "2.4", 0.24),
    ("GDP growth", "1.5", 0.15),
    ("Rate", "50", 49.5),
    ("Share", "12", 11.88),
    ("\uc99d\uac00\uc728", "5.2", 0.51),
])
def test_a_plain_column_is_read_at_its_own_scale(column, cell, difference):
    """The digits of "5.2%" mean 5.2 percent in a plain "inflation" column.

    Reading every plain column as fractions made "The rate was 4.2%" a near
    mismatch 415.8 away from its own cell, which is worse than no answer: the
    column's own values say what its two-decimal numbers mean.
    """
    number = only("the figure was %s%%" % cell, table([column], [[cell]]))
    assert number["status"] == "match", column
    assert number["matches"][0]["cell"] == cell
    assert number["matches"][0]["column_scale"] == "percent"
    assert number["matches"][0]["scale_source"] == "inferred from values"


def test_the_unemployment_rate_case_names_its_own_cell():
    """The exact input from the review: 4.2 against a plain 4.2 column."""
    number = only("The unemployment rate was 4.2%",
                  table(["Unemployment"], [["4.2"], ["4.6"]]))
    assert number["status"] == "match"
    assert number["matches"][0]["cell"] == "4.2"
    assert number["nearest"] is None


def test_the_scale_fields_are_null_when_no_scale_was_used():
    """A count column's "percent" would only say "not fractions", so it is null."""
    count_match = only("100 permits", table(["Permits"], [["100"]]))["matches"][0]
    assert count_match["column_scale"] is None
    assert count_match["scale_source"] is None
    money_match = only("$1200000", table(["Cost"], [["1200000"]]))["matches"][0]
    assert money_match["column_scale"] is None
    assert money_match["scale_source"] is None


def test_the_scale_fields_are_reported_when_a_percent_claim_used_them():
    percent_match = only("5.2%", table(["Change"], [["5.2"]]))["matches"][0]
    assert percent_match["column_scale"] == "percent"
    assert percent_match["scale_source"] == "inferred from values"


@pytest.mark.parametrize("header, scale", [("Change (%)", "percent"),
                                          ("Change (fraction)", "fraction")])
def test_a_declared_scale_is_reported_for_any_claim(header, scale):
    """A header that names a scale is reported even for a plain claim."""
    match = only("0.052", table([header], [["0.052"]]))["matches"][0]
    assert match["column_scale"] == scale
    assert match["scale_source"] == "header"


def test_the_nearest_entry_also_nulls_an_unused_scale():
    """A percent claim near a plain column used its inferred scale; a count
    claim near a plain column used none."""
    count_near = only("a total of 1204", table(["Permits"], [["1200"]]))["nearest"]
    assert count_near["column_scale"] is None
    assert count_near["scale_source"] is None
    percent_near = only("5.2%", table(["Change (%)"], [["5.4"]]))["nearest"]
    assert percent_near["column_scale"] == "percent"
    assert percent_near["scale_source"] == "header"


@pytest.mark.parametrize("header", ["Change (%)", "Change (percent)", "Change pct",
                                    "Change (\uff05)", "Change (pp)",
                                    "\ubcc0\ud654\uc728 \ud37c\uc13c\ud2b8"])
def test_a_header_naming_a_percent_scale_is_read_as_percent(header):
    number = only("a change of 5.2%", table([header], [["5.2"]]))
    assert number["status"] == "match", header
    assert number["matches"][0]["column_scale"] == "percent"
    assert number["matches"][0]["scale_source"] == "header"


@pytest.mark.parametrize("header", ["Change (fraction)", "Share of 1", "proportion",
                                    "Ratio (0-1)", "\ube44\uc728", "\ube44\uc911"])
def test_a_header_naming_a_fraction_scale_is_read_as_fractions(header):
    number = only("a change of 5.2%", table([header], [["0.052"]]))
    assert number["status"] == "match", header
    assert number["matches"][0]["column_scale"] == "fraction"
    assert number["matches"][0]["scale_source"] == "header"


def test_a_fraction_column_is_inferred_from_its_values():
    """Every value within [-1, 1] and one non-integer: fractions."""
    fractions = table(["Change"], [["0.052"], ["0.061"], ["0"]])
    number = only("a change of 5.2%", fractions)
    assert number["status"] == "match"
    assert number["matches"][0]["cell"] == "0.052"
    assert number["matches"][0]["column_scale"] == "fraction"
    assert number["matches"][0]["scale_source"] == "inferred from values"


def test_an_all_integer_column_of_small_numbers_is_still_a_percent_scale():
    """0 and 1 alone are not enough to call a column fractions."""
    number = only("a change of 50%", table(["Change"], [["0"], ["1"], ["50"]]))
    assert number["status"] == "match"
    assert number["matches"][0]["column_scale"] == "percent"


def test_the_zero_only_column_is_percent_scale():
    assert only("0%", table(["Change"], [["0"]]))["matches"][0]["column_scale"] == "percent"


def test_a_fraction_column_by_header_still_reports_the_scale_confusion():
    """The round-1 case: "0.3%" against a fraction column holding 0.3."""
    number = only("0.3%", table(["Change (fraction)"], [["0.3"]]))
    assert number["status"] == "near_mismatch"
    assert number["nearest"]["reason"] == "scale differs (percent vs fraction)"
    assert number["nearest"]["column_scale"] == "fraction"


def test_the_scale_confusion_holds_for_an_inferred_fraction_column_too():
    """0.3 and 0.45 are all inside [-1, 1] with a non-integer, so fractions."""
    number = only("0.3%", table(["Change"], [["0.3"], ["0.45"]]))
    assert number["status"] == "near_mismatch"
    assert number["nearest"]["reason"] == "scale differs (percent vs fraction)"
    assert number["nearest"]["scale_source"] == "inferred from values"


def test_a_percent_claim_still_reaches_a_fraction_column_at_value_over_100():
    assert only("30%", table(["Change"], [["0.3"], ["0.45"]]))["status"] == "match"
    assert only("30%", table(["Change (fraction)"], [["0.3"]]))["status"] == "match"


def test_a_plain_claim_is_not_read_through_the_columns_percent_scale():
    """A count is counted in the table's units, so 100 is not one permit."""
    assert only("1 permit", table(["Permits (%)"], [["100"]]))["status"] == "not_found"
    assert only("100 permits", table(["Permits (%)"], [["100"]]))["status"] == "match"
    assert only("1 permit", table(["Permits"], [["100"]]))["status"] == "not_found"
    assert only("100 permits", table(["Permits"], [["100"]]))["status"] == "match"


def test_a_bare_decimal_claim_still_reaches_the_fraction_of_a_percent_header():
    assert only("0.052", table(["Change (%)"], [["5.2"]]))["status"] == "match"
    assert only("0.052", table(["Change"], [["0.052"], ["0.12"]]))["status"] == "match"


def test_a_percent_claim_never_matches_a_money_amount():
    assert only("a share of 12%", table(["Cost"], [["12"]]))["status"] == "not_found"


def test_an_unlabelled_plain_column_still_breaks_the_row_tie():
    """An unlabelled plain column must stay covered, so this keeps the tie."""
    answer = reconcile("5.2%", table(["First", "Second"],
                                     [["5.2", "5.2"], ["5.2", "5.2"]]))
    matches = answer["numbers"][0]["matches"]
    assert [(m["row"], m["col"]) for m in matches] == [(0, 0), (0, 1), (1, 0), (1, 1)]
    assert {m["column_scale"] for m in matches} == {"percent"}


def test_an_unlabelled_plain_column_still_names_the_same_nearest_cell():
    tied = table(["Change", "Other"], [["6.0", "6.0"]])
    first = only("a fall of 5.9%", tied)
    second = only("a fall of 5.9%", tied)
    assert first == second
    assert first["nearest"]["col"] == 0
    assert first["nearest"]["difference"] == pytest.approx(0.1)


# --------------------------------------- multiplier letter (regression R2)
def test_a_bare_condensed_multiplier_still_scales():
    """"5K respondents" is 5,000: a letter multiplier needs no currency."""
    number = only("5K respondents", table(["Respondents"], [["5"]]))
    assert number["value"] == 5000.0
    assert number["kind"] == "count"
    assert number["matches"] == []
    assert only("5K respondents", table(["Respondents"], [["5000"]]))["status"] == "match"


def test_five_metres_is_a_measurement_not_money_and_not_millions():
    """A bare "5m" beside a spatial word is ambiguous: never money, never 5e6."""
    number = only("the site is 5m wide", table(["Width"], [["5"]]))
    assert number["kind"] == "other"
    assert number["value"] is None
    assert number["status"] == "not_found"
    assert number["reason"] == "ambiguous unit: m may mean metres or million"


# ------------------------------------ lowercase m, three ways (regression R2)
@pytest.mark.parametrize("claim, value", [
    ("$5m", 5000000.0),
    ("$1.5m", 1500000.0),
    ("\u00a35m", 5000000.0),
    ("\u20ac1.2m", 1200000.0),
    ("5m USD", 5000000.0),
    ("5m dollars", 5000000.0),
    ("5m euros", 5000000.0),
])
def test_a_lowercase_m_with_a_currency_is_millions_of_money(claim, value):
    """A currency before or after the "m" settles it: $5m is five million."""
    number = only(claim, table(["Cost"], [["1"]]))
    assert number["kind"] == "money", claim
    assert number["value"] == value, claim


@pytest.mark.parametrize("claim, value, kind", [
    ("20m people", 20000000.0, "count"),
    ("5m users", 5000000.0, "count"),
    ("12m viewers", 12000000.0, "count"),
    ("3m people", 3000000.0, "count"),
    ("1.5m cases", 1500000.0, "decimal"),
])
def test_a_lowercase_m_before_a_countable_noun_is_millions_of_things(claim, value, kind):
    """No currency, but a noun that is not spatial: a count in millions. The
    kind follows the written form, as it does everywhere: a number written with
    a decimal point is a decimal, exactly as "1.2 million" is."""
    number = only(claim, table(["n"], [["1"]]))
    assert number["value"] == value, claim
    assert number["kind"] == kind, claim


def test_twenty_million_people_no_longer_matches_twenty_people():
    """The exact regression: 20 against a cell of 20 was a clean match."""
    number = only("20m people", table(["People"], [["20"]]))
    assert number["status"] == "not_found"
    assert number["matches"] == []
    assert only("20m people", table(["People"], [["20000000"]]))["status"] == "match"


@pytest.mark.parametrize("claim", ["5m", "a span of 5m", "5m long", "5m wide", "5m high",
                                   "5m deep", "5m from the wall", "5m of pipe",
                                   "5m per person"])
def test_an_ambiguous_lowercase_m_is_reported_and_never_matched(claim):
    """"5m" alone may be metres or millions, so it produces no match at all."""
    number = only(claim, table(["Width"], [["5"], ["5000000"]]))
    assert number["status"] == "not_found", claim
    assert number["matches"] == [], claim
    assert number["nearest"] is None, claim
    assert number["kind"] == "other", claim
    assert number["reason"] == "ambiguous unit: m may mean metres or million", claim
    assert number["value"] is None, claim


@pytest.mark.parametrize("claim", ["5m and 6m", "5m to 6m"])
def test_a_bare_m_before_a_conjunction_stays_ambiguous(claim):
    """A conjunction is no countable noun: "5m and 6m" is not five million."""
    numbers = reconcile(claim, table(["Width"], [["5"], ["5000000"]]))["numbers"]
    assert [n["value"] for n in numbers] == [None, None], claim
    assert [n["reason"] for n in numbers] == ["ambiguous unit: m may mean metres or million"] * 2


# ------------------------------- the count-noun list behind a bare "m" (R2b)
@pytest.mark.parametrize("noun", [
    "gap", "beam", "width", "length", "height", "depth", "corridor", "drop",
    "wall", "room", "clearing", "clearance", "trench", "bridge", "pipe",
    "pole", "fence", "hole", "track", "thickness",
])
def test_a_measurement_noun_after_a_bare_m_is_ambiguous(noun):
    """Only a closed set of count nouns makes a bare "m" mean millions.

    "a 5m gap" is a five-metre gap, not five million gaps. Every unlisted noun
    leaves the "m" ambiguous, so it never matches and never invents a near miss.
    """
    claim = "a 5m " + noun
    number = only(claim, table([noun.title()], [["5"], ["4000000"], ["5000000"]]))
    assert number["value"] is None, claim
    assert number["kind"] == "other", claim
    assert number["status"] == "not_found", claim
    assert number["matches"] == [], claim
    assert number["nearest"] is None, claim
    assert number["reason"] == "ambiguous unit: m may mean metres or million", claim


def test_a_korean_measure_word_after_a_bare_m_is_ambiguous():
    """Korean "5m 폭" is a five-metre width, not five million of anything."""
    number = only("5m 폭", table(["폭"], [["5"], ["5000000"]]))
    assert number["value"] is None
    assert number["status"] == "not_found"
    assert number["matches"] == [] and number["nearest"] is None


@pytest.mark.parametrize("claim", ["a 5m gap", "the beam was 5m"])
def test_a_measurement_near_miss_is_never_fabricated(claim):
    """The reported defect: a real 5 and a fabricated near miss against 4e6."""
    numbers = reconcile(claim, table(["Length"], [["5000000"], ["4000000"]]))["numbers"]
    assert [n["nearest"] for n in numbers] == [None]
    assert numbers[0]["matches"] == []


@pytest.mark.parametrize("claim,noun", [
    ("20m people", "people"), ("5m users", "users"), ("12m viewers", "viewers"),
    ("1.5m cases", "cases"), ("3m people", "people"),
])
def test_a_count_noun_after_a_bare_m_is_still_millions(claim, noun):
    """The closed list must keep working: these are counts in millions.

    The kind follows the written form, so "1.5m cases" is a decimal of 1,500,000
    rather than a count — the multiplier, not the noun, decides that.
    """
    number = only(claim, table([noun.title()], [["20000000"], ["5000000"],
                                                ["12000000"], ["1500000"], ["3000000"]]))
    assert number["status"] == "match", claim
    assert number["matches"][0]["value"] == number["value"], claim
    assert number["kind"] in ("count", "decimal"), claim


@pytest.mark.parametrize("claim,counter", [
    ("5m명", "명"), ("5m명이", "명"), ("5m명의", "명"), ("국민 5m명이 참여했다", "명"),
    ("5m개가", "개"), ("5m건이", "건"), ("5m가구가", "가구"), ("5m회", "회"),
    ("5m 명", "명"), ("5m 가구", "가구"),
])
def test_a_korean_counter_after_a_bare_m_is_millions(claim, counter):
    number = only(claim, table([counter], [["5000000"]]))
    assert number["status"] == "match", claim


@pytest.mark.parametrize("claim", ["5m건물", "5m개월", "5m명단"])
def test_a_korean_word_that_merely_starts_with_a_counter_stays_ambiguous(claim):
    number = only(claim, table(["n"], [["5000000"], ["5"]]))
    assert number["value"] is None and number["status"] == "not_found", claim

def test_an_unambiguous_number_carries_no_reason():
    assert only("5 permits", table(["n"], [["5"]]))["reason"] is None


# ------------------------------------------- the year mask (regression R3)
def test_two_thousand_firms_is_a_quantity_not_a_year():
    """"2 thousand" is 2,000 by its written form, which is no year."""
    number = only("2 thousand firms took part", table(["Firms"], [["2000"]]))
    assert number["value"] == 2000.0
    assert number["status"] == "match"


@pytest.mark.parametrize("claim, value", [
    ("1 thousand samples", 1000.0),
    ("2 thousand firms", 2000.0),
    ("2.0 thousand firms", 2000.0),
    ("2,000 firms", 2000.0),
    ("2 million firms", 2000000.0),
])
def test_a_written_year_range_needs_the_written_form(claim, value):
    assert only(claim, table(["n"], [["1"]]))["value"] == value


@pytest.mark.parametrize("claim", ["1900", "2024", "2100"])
def test_a_bare_four_digit_year_is_still_ignored(claim):
    assert reconcile("In %s the policy was adopted" % claim,
                     table(["n"], [["1"]]))["numbers"] == []


# ------------------------------------------- the reference masks (regression R4)
def test_an_ordinary_no_keeps_its_figure():
    """"no" is the ordinary English word; only "No." and "No" are references."""
    number = only("There was no 5% increase", table(["Change (%)"], [["5"]]))
    assert number["value"] == 5.0
    assert number["status"] == "match"


@pytest.mark.parametrize("claim, expected", [
    ("no increase of 3%", [3.0]),
    ("the notes recorded 12 permits", [12.0]),
    ("parts of the 4% sample", [4.0]),
    ("There was no 5% increase", [5.0]),
])
def test_plain_words_no_longer_swallow_a_figure(claim, expected):
    values = [n["value"] for n in reconcile(claim, table(["n"], [["1"]]))["numbers"]]
    assert values == expected, claim


@pytest.mark.parametrize("claim", ["No. 4", "Nos. 4", "NO. 4", "#4"])
def test_a_reference_label_is_still_masked(claim):
    assert reconcile(claim, table(["n"], [["4"]]))["numbers"] == []


@pytest.mark.parametrize("claim", ["No 4", "NO 4"])
def test_a_bare_capitalised_no_is_not_a_reference(claim):
    """Only "No." with its dot or "#" labels a reference. The trade-off is that
    "No 4 shows it" reads its 4 as a quantity: a reference spelled without its
    dot cannot be told from the ordinary word."""
    values = [n["value"] for n in reconcile(claim, table(["n"], [["1"]]))["numbers"]]
    assert values == [4.0], claim


@pytest.mark.parametrize("claim, expected, kind", [
    ("No 5.2% change was found.", [5.2], "percent"),
    ("No 5% increase was found.", [5.0], "percent"),
    ("NO 5.2% change", [5.2], "percent"),
    ("No 12 firms responded.", [12.0], "count"),
    ("No 1,204 firms responded.", [1204.0], "count"),
    ("No 0.03 p-value", [0.03], "decimal"),
    ("No 4 shows it", [4.0], "count"),
])
def test_a_sentence_initial_no_keeps_the_figure_it_qualifies(claim, expected, kind):
    """The exact regression: "No" as a label head swallowed a real figure.

    A capitalised "No" opens a sentence as often as it labels a reference, and
    a claim that means a reference writes "No." with its dot or "#"."""
    numbers = reconcile(claim, table(["n"], [["1"]]))["numbers"]
    assert [n["value"] for n in numbers] == expected, claim
    assert [n["kind"] for n in numbers] == [kind], claim


@pytest.mark.parametrize("claim, expected", [
    ("In Table 2, 1,204 firms were surveyed", [1204.0]),
    ("\ud45c 2, 1,204\uac1c \uae30\uc5c5", [1204.0]),
    ("Table 2, 3 firms", [3.0]),
    ("Tables 2 and 3 report 1,204 firms", [1204.0]),
    ("Tables 2, 3 and 4 report 1,204 firms", [1204.0]),
    ("Tables 2, 3, and 4 report 1,204 firms", [1204.0]),
])
def test_a_label_list_continues_only_through_list_syntax(claim, expected):
    values = [n["value"] for n in reconcile(claim, table(["n"], [["1"]]))["numbers"]]
    assert values == expected, claim


@pytest.mark.parametrize("claim", [
    "Tables 2 and 3", "Tables 2-3", "Tables 2, 3 and 4",
    "Figures 4 and 5", "Appendix 2, 3 and 4", "Sections 3, 4, 5 and 6",
])
def test_a_label_list_of_bare_integers_is_masked_whole(claim):
    assert reconcile(claim, table(["n"], [["1"]]))["numbers"] == [], claim


def test_a_label_list_member_with_a_unit_is_not_a_list_member():
    """"Table 2 and 5.2%" keeps its figure: 5.2% is not a bare integer."""
    values = [n["value"] for n in reconcile("Table 2 and 5.2% of firms",
                                            table(["n"], [["1"]]))["numbers"]]
    assert values == [5.2]


@pytest.mark.parametrize("claim, expected", [
    ("(12) participants enrolled", [12.0]),
    ("enrolled (12) firms", [12.0]),
    ("(12) Participants enrolled", [12.0]),
])
def test_a_parenthesised_count_before_a_noun_is_kept(claim, expected):
    values = [n["value"] for n in reconcile(claim, table(["n"], [["1"]]))["numbers"]]
    assert values == expected, claim


@pytest.mark.parametrize("claim", ["see (12)", "firms (12).", "(12)", "firms (12)"])
def test_a_citation_like_parenthesised_integer_is_masked(claim):
    assert reconcile(claim, table(["n"], [["1"]]))["numbers"] == [], claim


# --------------------------------------------- other unit ranges (regression R5)
@pytest.mark.parametrize("claim, expected", [
    ("5 pp\u20136 pp", [(5.0, "other"), (6.0, "other")]),
    ("5 p.p.\u20136 p.p.", [(5.0, "other"), (6.0, "other")]),
    ("5 km\u20136 km", [(5.0, "count"), (6.0, "count")]),
    ("5 pounds\u20136 pounds", [(5.0, "money"), (6.0, "money")]),
    ("5 months\u20136 months", [(5.0, "count"), (6.0, "count")]),
])
def test_a_range_with_any_single_unit_word_keeps_both_bounds(claim, expected):
    """A dash between a number with a unit and a number is a range separator."""
    numbers = reconcile(claim, table(["n"], [["1"]]))["numbers"]
    assert [(n["value"], n["kind"]) for n in numbers] == expected, claim


@pytest.mark.parametrize("claim", ["5 pp", "5 p.p.", "5 pp.", "5 %p"])
def test_percentage_points_are_their_own_kind(claim):
    number = only(claim, table(["n"], [["5"]]))
    assert number["kind"] == "other", claim
    assert number["value"] == 5.0, claim


@pytest.mark.parametrize("claim", ["pp. 12-14", "p. 12", "pp. 7"])
def test_a_page_reference_is_not_a_percentage_point(claim):
    assert reconcile(claim, table(["n"], [["5"]]))["numbers"] == [], claim


def test_a_dash_after_an_ordinary_word_is_still_a_sign():
    """One unit word may be skipped, so "the fall was -5%" keeps its minus."""
    number = only("the fall was -5%", table(["Change (%)"], [["5"]]))
    assert number["value"] == -5.0
    assert number["status"] == "near_mismatch"
    assert "sign differs" in number["nearest"]["reason"]


# -------------------------------------------------------------- documented
def test_the_docstring_and_the_code_agree_about_a_bare_multiplier():
    """The module docs call "1.2 million" a decimal of 1,200,000, as it is."""
    import app.numeric_check as module

    assert "1.2 million" in module.__doc__ and "decimal" in module.__doc__
    number = only("1.2 million people", table(["People"], [["1200000"]]))
    assert number["kind"] == "decimal"
    assert number["value"] == 1200000.0
    assert number["status"] == "match"
