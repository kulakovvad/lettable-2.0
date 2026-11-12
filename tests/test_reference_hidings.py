import json
from pathlib import Path

import pytest

from core.pdf import page_texts
from reference import acuitus_parser, build_labels, selfcheck

FIXTURE = Path("fixtures/labels.json")
PDF = next(Path("data/pdfs").glob("acuitus-90f42467_*.pdf"), None)
NOT_PARSED = {"address", "property_type"}

pytestmark = pytest.mark.skipif(PDF is None, reason="Hidings brochure not available locally")


@pytest.fixture(scope="module")
def labels():
    return acuitus_parser.parse(PDF, "acuitus-90f42467")


def test_values_match_fixture(labels):
    expected = {(lb["unit_id"], lb["field"]): lb["value"]
                for lb in json.loads(FIXTURE.read_text()) if lb["field"] not in NOT_PARSED}
    got = {(lb.unit_id, lb.field): lb.value for lb in labels}
    assert got == expected


def test_every_label_has_verbatim_quote_on_its_page(labels):
    pages = page_texts(PDF)
    for lb in labels:
        assert lb.page == 3 and lb.label_source == "parser"
        text = " ".join(pages[lb.page - 1].split())
        assert " ".join(lb.quote.split()) in text, (lb.unit_id, lb.field, lb.quote)


def test_hidings_is_agreed(labels):
    assert selfcheck.check_status(labels, "acuitus-90f42467") == ("agreed", [])


def test_parse_tenancy_alias(labels):
    assert len(acuitus_parser.parse_tenancy(PDF)) == len(labels)


def test_build_one_reports_breaks_and_steps():
    _, row = build_labels.build_one(PDF)
    assert row["status"] == "agreed" and row["units"] == 4
    assert row["breaks"] == 2 and row["stepped_rent"] is True


def test_demo_corpus_prefers_rich_agreed_docs():
    report = [
        {"doc_id": "a", "status": "agreed", "units": 1, "breaks": 0, "stepped_rent": False, "labels": 9},
        {"doc_id": "b", "status": "agreed", "units": 4, "breaks": 2, "stepped_rent": True, "labels": 34},
        {"doc_id": "c", "status": "disputed", "units": 9, "breaks": 3, "stepped_rent": True, "labels": 80},
    ]
    assert build_labels.demo_corpus(report, size=2) == ["b", "a"]
