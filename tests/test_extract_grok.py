from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.models import PROPERTY_FIELDS, UNIT_FIELDS, ExtractionRun, Fact
from extract import common, grok, runner

DOC = "acuitus-90f42467"


def _cell(value, quote=None, page=3):
    return {"value": value, "quote": quote if quote is not None else value, "page": page if value else None}


RAW = {
    "model": "grok-4.6",
    "usage": {"prompt_tokens": 1000, "completion_tokens": 200, "total_tokens": 1200},
    "payload": {
        "units": [
            {
                "unit_id": "Unit 2",
                "tenant": _cell("HOWDEN JOINERY LIMITED"),
                "tenant_crn": _cell("00526923", "(CRN 00526923)"),
                "area_sqft": _cell("1,562", "(1,562)"),
                "passing_rent": _cell("22300", "£22,300"),
                "lease_start": _cell("29/09/2026", "7 years from 29/09/2026"),
                "lease_term_years": _cell("7", "7 years from 29/09/2026"),
                "lease_expiry": _cell("31/08/2033", "(31/08/2033)"),
                "break_date": _cell(None),
                "rent_review_date": _cell("29/09/2031"),
            }
        ],
        "property": {
            "address": _cell("Unit 1 to 4 The Hidings, Cirencester", page=1),
            "property_type": _cell("Industrial", page=1),
            "total_rent": _cell("£110,800", "£110,800 per Annum Exclusive", page=2),
            "total_area_sqft": _cell(None),
            "niy_pct": _cell("null"),
        },
    },
}


def test_coerce_types():
    assert common.coerce("passing_rent", "£22,300") == 22300
    assert common.coerce("area_sqft", "1,562.4") == 1562
    assert common.coerce("lease_term_years", "7") == 7.0
    assert common.coerce("tenant_crn", "526923") == "00526923"
    assert common.coerce("tenant_crn", "00526923") == "00526923"
    assert common.coerce("niy_pct", "null") is None
    assert common.coerce("lease_start", "29/09/2026") == "29/09/2026"
    assert common.coerce("passing_rent", "n/a") is None


def test_parse_facts():
    facts = grok.parse(RAW, DOC, "a", "run-1")
    by = {(f.unit_id, f.field): f for f in facts}
    assert by[("Unit 2", "passing_rent")].value == 22300
    assert by[("Unit 2", "passing_rent")].unit == "GBP/yr"
    assert by[("Unit 2", "tenant_crn")].value == "00526923"
    assert by[("Unit 2", "lease_term_years")].value == 7.0
    assert ("Unit 2", "break_date") not in by
    assert by[(None, "total_rent")].value == 110800
    assert by[(None, "total_rent")].page == 2
    assert (None, "niy_pct") not in by
    for f in facts:
        assert isinstance(f, Fact)
        assert f.extractor == "grok"
        assert f.author == "grok-4.6@grok-a-v1"
        assert f.run_id == "run-1"
        assert f.quote and f.page
        assert (f.unit_id is None) == (f.field in PROPERTY_FIELDS)


def test_variant_b_extractor_and_schema():
    facts = grok.parse(RAW, DOC, "b", "run-2")
    assert {f.extractor for f in facts} == {"grok-b"}
    sa, sb = grok.json_schema("a"), grok.json_schema("b")
    unit_a = sa["properties"]["units"]["items"]["required"]
    unit_b = sb["properties"]["units"]["items"]["required"]
    assert set(unit_a) == set(unit_b) == {"unit_id", *UNIT_FIELDS}
    assert unit_a != unit_b


def test_top_level_property_payload():
    payload = {"units": [], **RAW["payload"]["property"]}
    facts = common.facts_from_payload(payload, DOC, extractor="v7go", author="v7go@p",
                                      model_version="v7go@p", run_id=None)
    assert {f.field for f in facts} == {"address", "property_type", "total_rent"}
    assert all(f.unit_id is None for f in facts)


def test_duplicate_unit_ids_kept_apart():
    payload = {"units": [{"unit_id": "Unit 1", "tenant": _cell("A")}, {"unit_id": "Unit 1", "tenant": _cell("B")}]}
    facts = common.facts_from_payload(payload, DOC, extractor="grok", author="x", model_version="x", run_id=None)
    assert sorted(f.unit_id for f in facts) == ["Unit 1", "Unit 1 (2)"]


def test_extract_grok_reads_cache_without_network(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(common, "RAW_DIR", tmp_path / "raw")
    common.write_cache("grok", DOC, RAW)

    def boom(*a, **k):
        raise AssertionError("network must not be called when cache exists")

    monkeypatch.setattr(grok, "call_grok", boom)
    monkeypatch.setenv("GROK_PRICE_IN_PER_M", "3")
    monkeypatch.setenv("GROK_PRICE_OUT_PER_M", "15")
    facts, run = grok.extract_grok(tmp_path / f"{DOC}_the-hidings.pdf")
    assert run.extractor == "grok" and run.tokens == 1200 and run.doc_ids == [DOC]
    assert run.cost == pytest.approx(0.003 + 0.003)
    assert all(f.run_id == run.id for f in facts)


def test_doc_id_for():
    assert common.doc_id_for("data/pdfs/acuitus-90f42467_unit-1-to-4-the-hidings.pdf") == DOC


def test_runner_isolates_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "FACTS_DIR", tmp_path / "facts")
    monkeypatch.setattr(runner, "RUNS_DIR", tmp_path / "runs")
    good = Path("acuitus-aaaa1111_x.pdf")
    bad = Path("acuitus-bbbb2222_y.pdf")

    def fake(p: Path):
        if p == bad:
            raise RuntimeError("boom")
        run = ExtractionRun(extractor="grok", model="m", prompt_version="v", tokens=10, doc_ids=[common.doc_id_for(p)])
        return grok.parse(RAW, common.doc_id_for(p), "a", run.id), run

    merged, errors = runner.run_extractor("grok", [good, bad], workers=2, fn=fake)
    assert list(errors) == ["acuitus-bbbb2222"]
    assert merged.doc_ids == ["acuitus-aaaa1111"] and merged.tokens == 10
    saved = json.loads((tmp_path / "facts" / "grok" / "acuitus-aaaa1111.json").read_text())
    assert saved and saved[0]["doc_id"] == "acuitus-aaaa1111"
    assert json.loads((tmp_path / "runs" / "grok.json").read_text())["errors"]


def test_read_corpus(tmp_path):
    c = tmp_path / "corpus.txt"
    c.write_text("# comment\n\nacuitus-90f42467_x.pdf\nacuitus-12c3bfaf_y.pdf\n")
    assert [p.name for p in runner.read_corpus(c, limit=1)] == ["acuitus-90f42467_x.pdf"]
