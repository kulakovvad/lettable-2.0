from app.components.pdf_view import frame, show_value
from app.data import change_story
from app.pages_.proforma import _rent_roll, _tree_lines
from core.models import Cell


def test_show_value_and_frame_stringify_mixed_values():
    assert show_value(None) == ""
    assert show_value(25500.0) == "25500"
    df = frame([{"value": 22300}, {"value": "Howden Joinery Limited"}])
    assert list(df["value"]) == ["22300", "Howden Joinery Limited"]


def test_rent_roll_groups_unit_cells():
    cells = [Cell(version_id="v", name="passing_rent[Unit 1]", formula="fact", value=44500),
             Cell(version_id="v", name="break_date[Unit 1]", formula="fact", value="2027-06-01"),
             Cell(version_id="v", name="total_rent", formula="sum", value=44500)]
    roll = _rent_roll(cells)
    assert len(roll) == 1
    assert roll[0]["unit"] == "Unit 1" and roll[0]["passing_rent"] == "44500"
    assert roll[0]["break_date"] == "2027-06-01"


def test_tree_lines_collect_facts_with_pages():
    tree = {"kind": "cell", "name": "total_rent", "value": 44500, "unit": "GBP/yr",
            "formula": "sum(passing_rent[*])", "author": None, "reason": None,
            "children": [{"kind": "fact", "id": "f1", "name": "passing_rent[Unit 1]",
                          "value": 44500, "unit": "GBP/yr", "extractor": "v7go",
                          "author": "v7go@fixture", "created_at": "2026-11-12T21:00:00",
                          "page": 3, "quote": "£44,500", "reason": None, "children": []}]}
    facts: list[dict] = []
    lines = _tree_lines(tree, 0, facts)
    assert lines[0].startswith("- **total_rent**")
    assert "page 3" in lines[1] and "£44,500" in lines[1]
    assert [f["id"] for f in facts] == ["f1"]


def test_change_story_names_trigger_and_cells():
    rows = [{"name": "wault_break", "old": 4.23, "new": 0.98, "unit": "years", "version_no": 2,
             "author": "vadim", "reason": "footnote 2", "created_at": "2026-11-12T21:40:58",
             "trigger_fact": {"unit_id": "Unit 1", "field": "break_date", "old": None,
                              "new": "2027-06-01", "fact_id": "f2", "old_author": "v7go@fixture"}}]
    text = change_story(rows)
    assert "v2 was created by vadim" in text
    assert "Unit 1 break_date" in text and "2027-06-01" in text
    assert "wault_break: 4.23 years -> 0.98 years" in text
    assert change_story([]) == "No cell changed between these versions."
