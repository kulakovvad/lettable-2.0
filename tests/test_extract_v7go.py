import json

import pytest

from extract import v7go


def _raw(value, status="complete", grounding=None) -> dict:
    return {
        "id": "ent-1",
        "fields": {
            "brochure": {"status": "complete", "manual_value": {"value": None}, "tool_value": {"value": None}},
            v7go.JSON_SLUG: {
                "status": status,
                "error_message": None,
                "manual_value": {"value": None, "updated_by": None},
                "tool_value": {"value": value, "updated_by": None, "grounding": grounding},
            },
        },
    }


PAYLOAD = {
    "units": [{
        "unit_id": "Unit 1",
        "tenant": {"value": "Acme Ltd", "quote": "Acme Ltd", "page": 2},
        "passing_rent": {"value": "£12,500", "quote": "£12,500 p.a.x.", "page": 2},
        "tenant_crn": {"value": "526923", "quote": "CRN 00526923", "page": 2},
        "break_date": {"value": None, "quote": None, "page": None},
    }],
    "address": {"value": "1 High Street, Bath", "quote": "1 High Street, Bath", "page": 1},
}

FLAT = {
    "units": [{
        "unit_id": "Unit 1",
        "tenant": "Acme Ltd",
        "tenant_crn": "",
        "area_sqft": "(2,818) (1,324)",
        "passing_rent": "£44,500",
        "break_date": "24/08/2028, 24/08/2030",
        "rent_review_date": "29/09/2026, 29/09/2031",
    }],
    "total_rent": "£110,800",
    "niy_pct": "",
}

GROUNDING = {
    "sources": [
        {"index": 1, "bounding_boxes": [
            {"page": 3, "xmin": 1.0, "ymin": 2.0, "xmax": 3.0, "ymax": 2.5},
            {"page": 3, "xmin": 0.5, "ymin": 2.1, "xmax": 2.0, "ymax": 2.6},
            {"page": 4, "xmin": 0.0, "ymin": 0.0, "xmax": 9.0, "ymax": 9.0},
        ]},
        {"index": 2, "bounding_boxes": [{"page": 2, "xmin": 0.0, "ymin": 1.0, "xmax": 1.0, "ymax": 2.0}]},
    ],
    "claims": [
        {"object_path": "$.units[0].passing_rent", "source_indices": [1]},
        {"object_path": "$.units[0].area_sqft", "source_indices": [1]},
        {"object_path": "$.total_rent", "source_indices": [2]},
    ],
}


def test_parse_nested_value(monkeypatch):
    monkeypatch.setenv("V7_PROJECT_ID", "proj")
    facts = v7go.parse(_raw(PAYLOAD), "acuitus-test", "run-1")
    by = {(f.unit_id, f.field): f for f in facts}
    assert by[("Unit 1", "passing_rent")].value == 12500
    assert by[("Unit 1", "passing_rent")].page == 2
    assert by[("Unit 1", "tenant_crn")].value == "00526923"
    assert ("Unit 1", "break_date") not in by
    assert by[(None, "address")].quote == "1 High Street, Bath"
    assert all(f.extractor == "v7go" and f.author == "v7go@proj" and f.run_id == "run-1" for f in facts)


def test_parse_json_string_value(monkeypatch):
    monkeypatch.setenv("V7_PROJECT_ID", "proj")
    facts = v7go.parse(_raw(json.dumps(PAYLOAD)), "acuitus-test", "run-1")
    assert len(facts) == 4


def test_parse_flat_with_citations(monkeypatch):
    monkeypatch.setenv("V7_PROJECT_ID", "proj")
    monkeypatch.setenv("VALUATION_DATE", "2026-11-12")
    facts = v7go.parse(_raw(json.dumps(FLAT), grounding=GROUNDING), "acuitus-test", "run-1")
    by = {(f.unit_id, f.field): f for f in facts}
    rent = by[("Unit 1", "passing_rent")]
    assert rent.value == 44500 and rent.quote == "£44,500" and rent.page == 3
    assert rent.bbox == [36.0, 144.0, 216.0, 187.2]
    assert by[("Unit 1", "area_sqft")].value == 4142
    assert by[("Unit 1", "area_sqft")].quote == "(2,818) (1,324)"
    assert by[("Unit 1", "break_date")].value == "24/08/2028"
    assert by[("Unit 1", "rent_review_date")].value == "29/09/2031"
    assert by[("Unit 1", "rent_review_date")].page is None
    assert by[(None, "total_rent")].page == 2 and by[(None, "total_rent")].value == 110800
    assert ("Unit 1", "tenant_crn") not in by and (None, "niy_pct") not in by


def test_parse_failed_field_raises():
    with pytest.raises(RuntimeError):
        v7go.parse(_raw(None, status="failed"), "acuitus-test", None)


def test_parse_empty_entity():
    assert v7go.parse({"fields": {}}, "acuitus-test", None) == []
