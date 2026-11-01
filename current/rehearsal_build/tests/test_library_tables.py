"""Results tables in the research library, and their numeric reconciliation.

A table is an ordinary `library_items` row of kind `table`: it lists, orders,
moves and deletes like a note, and `load_table` turns its stored CSV back into
the `{item_id, title, columns, rows}` shape `app.numeric_check.reconcile`
compares a claim against. No schema change was needed, so the storage assertions
below also pin that no column was added.

These tests drive `app/library.py` directly rather than through HTTP: the
routes that expose `create_table` and `load_table` belong to the claim-check
wiring, not to this module, and the library functions are the contract it uses.

Every adversarial case is a real one: a BOM at the head of a file a spreadsheet
exported, CRLF line endings, a quoted comma inside a cell, a blank line, a
formula cell, and a table at the size ceiling.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from app import library, numeric_check
from app.harness import Conflict
from test_dot_safety import StubAdapter, h  # noqa: F401  (shared loopback-guarded harness fixture)


RESULTS = (
    "Outcome,Change (%),p-value,Permits (n),Cost (USD)\n"
    "Energy use,5.2,0.03,1204,1200000\n"
    "Water use,5.9,0.11,980,900000\n"
    "Waste,-0.3,0.4,300,45000\n"
)


def seed(h, kind="fx"):
    return h.register_project(kind)["project_id"]


def make(h, pid, csv_text=RESULTS, title="Main results", **extra):
    body = {"title": title, "csv_text": csv_text, **extra}
    return library.create_table(h, pid, body)


def save_screen_off(monkeypatch):
    """A table must be refused before any model call, so a call would be a bug."""
    from app import apple_ai

    def refuse(prompt, system):
        raise AssertionError("a table must never reach the on-device screen")

    monkeypatch.setattr(apple_ai, "_run", refuse)


# ------------------------------------------------------------------ storage
def test_a_table_is_an_ordinary_library_row_and_needs_no_schema_change(h):
    pid = seed(h)
    item = make(h, pid)
    assert item["kind"] == "table" and item["title"] == "Main results"
    assert item["pages"] == 3, "a table reports its data row count"
    row = h.db.execute("SELECT * FROM library_items WHERE id=?", (item["id"],)).fetchone()
    assert row["body_text"] == RESULTS, "the CSV lives in body_text"
    assert row["pdf_path"] is None and row["chars"] == len(RESULTS)
    columns = {c["name"] for c in h.db.execute("PRAGMA table_info('library_items')")}
    assert columns == {"id", "project_id", "folder_id", "kind", "title", "day", "body_text",
                       "pdf_path", "pages", "chars", "created_at"}


def test_a_table_appears_in_the_tree_like_other_items(h):
    pid = seed(h)
    note = library.create_note(h, {"project_id": pid, "text": "a note"})
    table = make(h, pid)
    tree = library.tree(h, pid)
    listed = {item["id"]: item for item in tree["items"]}
    assert set(listed) == {note["id"], table["id"]}
    assert listed[table["id"]]["kind"] == "table"
    assert "text" not in listed[table["id"]], "the CSV is not shipped in the tree"
    assert listed[note["id"]]["text"] == "a note"
    assert tree["folders"] == []


def test_a_table_moves_into_a_folder_and_survives_folder_deletion(h):
    pid = seed(h)
    fid = library.create_folder(h, pid, "Results")["id"]
    table = make(h, pid, folder_id=fid)
    assert table["folder_id"] == fid
    assert library.update_item(h, table["id"], {"title": "Renamed"})["title"] == "Renamed"
    removed = library.delete_folder(h, fid)
    assert removed["items_moved_to_root"] == 1
    listed = library.tree(h, pid)["items"][0]
    assert listed["id"] == table["id"] and listed["folder_id"] is None


def test_a_table_is_not_part_of_the_pdf_display_order(h):
    pid = seed(h)
    make(h, pid)
    # The order payload names every current PDF; with no PDF it is the empty
    # list, and a table is not a PDF to be placed.
    assert library.save_order(h, {"project_id": pid, "item_ids": []})["items"]


# ------------------------------------------------------------------ load_table
def test_load_table_returns_the_contract_shape(h):
    pid = seed(h)
    item = make(h, pid)
    table = library.load_table(h, item["id"], pid)
    assert set(table) == {"item_id", "title", "columns", "rows"}
    assert table["item_id"] == item["id"] and table["title"] == "Main results"
    assert table["columns"] == ["Outcome", "Change (%)", "p-value", "Permits (n)", "Cost (USD)"]
    assert table["rows"][0] == ["Energy use", "5.2", "0.03", "1204", "1200000"]
    assert len(table["rows"]) == 3
    assert all(isinstance(cell, str) for row in table["rows"] for cell in row)


def test_load_table_is_project_scoped(h):
    here = seed(h, "fx")
    there = seed(h, "nyc")
    item = make(h, here)
    assert library.load_table(h, item["id"], here)["item_id"] == item["id"]
    with pytest.raises(KeyError):
        library.load_table(h, item["id"], there)
    with pytest.raises(KeyError):
        library.load_table(h, item["id"], "p-missing")
    # Without a project named it is a plain lookup of an id the caller has.
    assert library.load_table(h, item["id"])["item_id"] == item["id"]


def test_load_table_refuses_unknown_and_non_table_items(h):
    pid = seed(h)
    with pytest.raises(KeyError):
        library.load_table(h, "li-missing", pid)
    note = library.create_note(h, {"project_id": pid, "text": "not a table"})
    with pytest.raises(Conflict):
        library.load_table(h, note["id"], pid)


def test_a_table_of_another_project_is_not_reachable_at_all(h):
    here = seed(h, "fx")
    there = seed(h, "nyc")
    item = make(h, here)
    assert item["id"] not in [i["id"] for i in library.tree(h, there)["items"]]
    assert library.search(h, there, "Energy") == []
    with pytest.raises(KeyError):
        library.screen(h, {"project_id": there, "item_ids": [item["id"]]})


# ------------------------------------------------------------------ CSV handling
def test_a_bom_and_crlf_are_read_as_a_plain_table(h):
    pid = seed(h)
    crlf = "\ufeff" + RESULTS.replace("\n", "\r\n")
    item = make(h, pid, csv_text=crlf)
    table = library.load_table(h, item["id"], pid)
    assert table["columns"][0] == "Outcome", "the BOM is not part of the first header"
    assert len(table["rows"]) == 3
    assert all("\r" not in cell for row in table["rows"] for cell in row)


def test_a_bom_with_lone_cr_line_endings_is_read_too(h):
    pid = seed(h)
    item = make(h, pid, csv_text="\ufefflabel,change (%)\renergy,5.2\rwater,5.9\r")
    assert library.load_table(h, item["id"], pid)["rows"] == [["energy", "5.2"], ["water", "5.9"]]


def test_a_quoted_comma_stays_inside_its_cell(h):
    pid = seed(h)
    quoted = 'label,change (%)\n"The energy, water and waste programme",5.2\n'
    table = library.load_table(h, make(h, pid, csv_text=quoted)["id"], pid)
    assert table["rows"][0] == ["The energy, water and waste programme", "5.2"]


def test_a_quoted_multiline_cell_stays_one_row(h):
    pid = seed(h)
    embedded = 'label,note,change (%)\nenergy,"first line\nsecond line",5.2\n'
    table = library.load_table(h, make(h, pid, csv_text=embedded)["id"], pid)
    assert table["rows"] == [["energy", "first line\nsecond line", "5.2"]]


def test_blank_lines_are_not_data_rows(h):
    pid = seed(h)
    sparse = "label,change (%)\nenergy,5.2\n\n,\nwater,5.9\n"
    item = make(h, pid, csv_text=sparse)
    assert len(library.load_table(h, item["id"], pid)["rows"]) == 2
    assert item["pages"] == 2


def test_surrounding_whitespace_in_a_header_is_trimmed(h):
    pid = seed(h)
    table = library.load_table(h, make(h, pid, csv_text=" label , change \nenergy,5.2\n")["id"], pid)
    assert table["columns"] == ["label", "change"]


# ------------------------------------------------------------------ rejections
@pytest.mark.parametrize("csv_text, message", [
    ("", "empty"),
    ("   \n  ", "empty"),
    (",\n", "header"),
    ("a,,b\n1,2,3\n", "header"),
    ("a,a\n1,2\n", "share a header"),
    ("a,b\n1\n", "cells"),
    ("a,b\n1,2,3\n", "cells"),
    ("only a header\n", "no data rows"),
    ("a,b\n1,=SUM(A1:A9)\n", "formula"),
    ("a,b\n=1+1,2\n", "formula"),
    ("a,b\n1,+A1\n", "formula"),
    ("a,b\n1,@cmd\n", "formula"),
    ("a,b\n1,-2*3\n", "formula"),
    ("a,b\n1,hello\x00world\n", "control characters"),
    ("=A1,change\nenergy,5.2\n", "formula"),
])
def test_a_table_that_is_not_csv_is_rejected(h, csv_text, message):
    pid = seed(h)
    with pytest.raises(Conflict) as raised:
        make(h, pid, csv_text=csv_text)
    assert message in str(raised.value)
    assert h.db.execute("SELECT count(*) c FROM library_items").fetchone()["c"] == 0


def test_a_plain_number_is_not_mistaken_for_a_formula(h):
    pid = seed(h)
    item = make(h, pid, csv_text="label,change (%)\nenergy,-0.3\nwater,+5.2%\nair,-\nzero,0\n")
    rows = library.load_table(h, item["id"], pid)["rows"]
    assert rows == [["energy", "-0.3"], ["water", "+5.2%"], ["air", "-"], ["zero", "0"]]


def test_size_bounds_are_enforced_before_anything_is_written(h, monkeypatch):
    pid = seed(h)
    row = "energy,5.2\n"

    monkeypatch.setattr(library, "MAX_TABLE_BYTES", 60)
    with pytest.raises(Conflict) as raised:
        make(h, pid, csv_text="label,change\n" + row * 10)
    assert "KB" in str(raised.value)
    monkeypatch.undo()

    many_rows = "label,change\n" + row * (library.MAX_TABLE_ROWS + 1)
    with pytest.raises(Conflict) as raised:
        make(h, pid, csv_text=many_rows)
    assert str(library.MAX_TABLE_ROWS) in str(raised.value)

    wide = ",".join(f"c{i}" for i in range(library.MAX_TABLE_COLUMNS + 1))
    with pytest.raises(Conflict) as raised:
        make(h, pid, csv_text=wide + "\n" + ",".join("1" * 1 for _ in range(51)) + "\n")
    assert str(library.MAX_TABLE_COLUMNS) in str(raised.value)
    assert h.db.execute("SELECT count(*) c FROM library_items").fetchone()["c"] == 0


def test_a_table_at_the_size_ceiling_is_accepted(h):
    """Both caps accept a table sitting exactly on them.

    The byte cap is the tighter of the two for wide data, so each bound is
    walked to its own edge: all 2,000 rows of a narrow table, and all 50
    columns of a shorter one.
    """
    pid = seed(h)
    tall = "label,change (%)\n" + "".join(f"row{index},5.2\n" for index in range(library.MAX_TABLE_ROWS))
    table = library.load_table(h, make(h, pid, csv_text=tall)["id"], pid)
    assert len(table["rows"]) == library.MAX_TABLE_ROWS

    columns = [f"c{index}" for index in range(library.MAX_TABLE_COLUMNS)]
    wide = ",".join(columns) + "\n" + ",".join(str(index % 10) for index in range(len(columns))) + "\n"
    table = library.load_table(h, make(h, pid, csv_text=wide)["id"], pid)
    assert len(table["columns"]) == library.MAX_TABLE_COLUMNS
    assert table["rows"] == [[str(index % 10) for index in range(len(columns))]]


def test_body_shape_is_checked(h):
    pid = seed(h)
    with pytest.raises(Conflict):
        library.create_table(h, pid, {"csv_text": RESULTS})
    with pytest.raises(Conflict):
        library.create_table(h, pid, {"title": "  ", "csv_text": RESULTS})
    with pytest.raises(Conflict):
        library.create_table(h, pid, {"title": "T", "csv_text": RESULTS, "extra": 1})
    with pytest.raises(Conflict):
        library.create_table(h, pid, {"title": "T", "csv_text": None})
    with pytest.raises(Conflict):
        library.create_table(h, pid, "not an object")
    with pytest.raises(KeyError):
        library.create_table(h, "p-missing", {"title": "T", "csv_text": RESULTS})
    with pytest.raises(KeyError):
        library.create_table(h, pid, {"title": "T", "csv_text": RESULTS, "folder_id": "lf-missing"})
    # A body naming one project cannot become a table in another.
    with pytest.raises(Conflict):
        library.create_table(h, pid, {"project_id": "p-other", "title": "T", "csv_text": RESULTS})


# ------------------------------------------------------------------ prose routes stay prose
def test_search_skips_a_table(h):
    pid = seed(h)
    make(h, pid)
    assert library.search(h, pid, "Energy") == []
    assert library.search(h, pid, "Outcome") == []
    library.create_note(h, {"project_id": pid, "text": "the energy result"})
    assert [hit["title"] for hit in library.search(h, pid, "energy")] == [
        "the energy result"]


def test_screen_refuses_a_table_without_sending_it(h, monkeypatch):
    pid = seed(h)
    save_screen_off(monkeypatch)
    item = make(h, pid)
    with pytest.raises(Conflict) as raised:
        library.screen(h, {"project_id": pid, "item_ids": [item["id"]]})
    assert "compared" in str(raised.value)


# ------------------------------------------------------------------ end to end
def test_a_claim_is_reconciled_against_a_stored_table(h):
    pid = seed(h)
    item = make(h, pid)
    table = library.load_table(h, item["id"], pid)
    answer = numeric_check.reconcile(
        "The policy cut energy use by 5.2% (p < 0.05) across 1,204 permits", table)
    assert answer["summary"] == {"total": 3, "match": 3, "near_mismatch": 0, "not_found": 0}
    assert [(m["row"], m["col"]) for m in answer["numbers"][0]["matches"]] == [(0, 1)]
    assert [(m["row"], m["col"]) for m in answer["numbers"][2]["matches"]] == [(0, 3)]


def test_a_korean_table_and_korean_claim_are_reconciled(h):
    pid = seed(h)
    item = make(h, pid, title="주요 결과",
                csv_text="항목,변화율 (%),p값,허가 건수\n에너지,5.2,0.03,1204\n용수,5.9,0.11,980\n")
    table = library.load_table(h, item["id"], pid)
    answer = numeric_check.reconcile("에너지 사용량이 5.2퍼센트 줄었고 p < 0.05, 허가 1,204건", table)
    assert answer["summary"] == {"total": 3, "match": 3, "near_mismatch": 0, "not_found": 0}


def test_a_wrong_figure_in_a_real_table_is_a_near_mismatch(h):
    pid = seed(h)
    table = library.load_table(h, make(h, pid)["id"], pid)
    answer = numeric_check.reconcile("The policy cut energy use by 6.1% across 1,204 permits", table)
    assert answer["numbers"][0]["status"] == "near_mismatch"
    assert answer["numbers"][0]["nearest"]["cell"] == "5.9"
    assert answer["numbers"][0]["nearest"]["column"] == "Change (%)"


# ------------------------------------------------------------------ no network
def test_the_table_module_imports_no_network_client():
    source = Path(library.__file__).read_text()
    for forbidden in ("requests", "httpx", "urllib.request", "openai", "anthropic", "http.client"):
        assert forbidden not in source
    numeric = Path(numeric_check.__file__).read_text()
    assert "import socket" not in numeric
    assert "import urllib" not in numeric and "import requests" not in numeric


# ------------------------------------------------------------------ regressions
def test_trailing_blank_lines_do_not_count_toward_the_row_bound(h):
    """The 2,000-row bound counts data rows, not the blank lines after them.

    A spreadsheet exports a trailing newline per row, so counting blanks made a
    table at the row ceiling unimportable.
    """
    pid = seed(h)
    rows = "".join(f"row{index},5.2\n" for index in range(library.MAX_TABLE_ROWS))
    csv_text = "label,change (%)\n" + rows + "\n\n   \n,\n" * 50
    item = make(h, pid, csv_text=csv_text)
    table = library.load_table(h, item["id"], pid)
    assert len(table["rows"]) == library.MAX_TABLE_ROWS


def test_blank_lines_between_rows_are_still_ignored(h):
    pid = seed(h)
    item = make(h, pid, csv_text="label,change (%)\nrow0,5.2\n\n\nrow1,5.9\n\n")
    assert library.load_table(h, item["id"], pid)["rows"] == [["row0", "5.2"], ["row1", "5.9"]]


def test_the_row_bound_still_rejects_one_data_row_too_many(h):
    pid = seed(h)
    rows = "".join(f"row{index},5.2\n" for index in range(library.MAX_TABLE_ROWS + 1))
    with pytest.raises(Conflict) as raised:
        make(h, pid, csv_text="label,change (%)\n" + rows)
    assert str(library.MAX_TABLE_ROWS) in str(raised.value)


def test_the_byte_bound_is_still_the_hard_cap_on_raw_input(h, monkeypatch):
    """Dropping blanks must not let raw input size go unchecked."""
    pid = seed(h)
    monkeypatch.setattr(library, "MAX_TABLE_BYTES", 80)
    with pytest.raises(Conflict) as raised:
        make(h, pid, csv_text="label,change (%)\nrow0,5.2\n" + "\n" * 200)
    assert "KB" in str(raised.value)


def test_negative_and_percent_cells_are_accepted_as_data(h):
    """Cells the number reader parses are never mistaken for formulas."""
    pid = seed(h)
    cells = ["-.5", "-.03", "-.5e-3", "-$1.2M", "-0.3 %", "+5.2 %", "-0.3", "+5.2%", "-"]
    csv_text = "label,change\n" + "".join(f"row{index},{cell}\n"
                                          for index, cell in enumerate(cells))
    table = library.load_table(h, make(h, pid, csv_text=csv_text)["id"], pid)
    assert [row[1] for row in table["rows"]] == cells


def test_save_order_tolerates_a_table_item(h):
    """The frontend reads any non-note kind as a PDF, so a table id can arrive.

    The choice: tolerate and ignore it. The PDF-only completeness check is
    unchanged, so tables keep their created_at position and no ordering
    semantics change.
    """
    pid = seed(h)
    table_item = make(h, pid)
    # No PDFs: the complete list of PDFs is empty, and the table id is ignored
    # rather than refused as an id "that is not a current PDF".
    answer = library.save_order(h, {"project_id": pid, "item_ids": [table_item["id"]]})
    assert [item["id"] for item in answer["items"]] == [table_item["id"]]
    assert answer["items"][0]["kind"] == "table"


def test_save_order_still_refuses_an_unknown_id(h):
    pid = seed(h)
    with pytest.raises(Conflict):
        library.save_order(h, {"project_id": pid, "item_ids": ["li-missing"]})


def test_save_order_still_refuses_a_note_id(h):
    """Only tables are tolerated; a note id was and remains refused."""
    pid = seed(h)
    note = library.create_note(h, {"project_id": pid, "text": "note"})
    with pytest.raises(Conflict):
        library.save_order(h, {"project_id": pid, "item_ids": [note["id"]]})


def test_save_order_places_pdfs_around_an_ignored_table(h):
    """The real payload mixes a table id among PDF ids.

    The PDFs must still be reordered exactly as before and the table must keep
    its created_at position, which is the whole point of ignoring it.
    """
    import base64

    pid = seed(h)
    pdf = b"%PDF-1.4\n1 0 obj<</Type/Catalog>>endobj\ntrailer<</Root 1 0 R>>\n%%EOF"
    encoded = base64.b64encode(pdf).decode()
    first = library.create_doc(h, {"project_id": pid, "filename": "a.pdf", "pdf_base64": encoded})
    second = library.create_doc(h, {"project_id": pid, "filename": "b.pdf", "pdf_base64": encoded})
    table_item = make(h, pid)
    answer = library.save_order(h, {"project_id": pid,
                                    "item_ids": [second["id"], table_item["id"], first["id"]]})
    kinds = [(item["id"], item["kind"]) for item in answer["items"]]
    assert kinds[0] == (table_item["id"], "table"), "the table keeps its place"
    pdfs = [item["id"] for item in answer["items"] if item["kind"] == "pdf"]
    assert pdfs == [second["id"], first["id"]], "the PDFs took the saved order"
    with pytest.raises(Conflict):
        library.save_order(h, {"project_id": pid, "item_ids": [second["id"]]})
