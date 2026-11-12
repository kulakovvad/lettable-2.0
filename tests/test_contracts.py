import json
from pathlib import Path

from core.models import ALL_FIELDS, Fact, Label

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _load(name: str) -> list[dict]:
    return json.loads((FIXTURES / name).read_text())


def test_fixture_facts_parse_and_have_quote_and_page() -> None:
    for name in ("facts_v7.json", "facts_grok.json"):
        facts = [Fact(**f) for f in _load(name)]
        assert facts
        assert all(f.field in ALL_FIELDS and f.quote and f.page for f in facts)


def test_labels_total_rent_matches_units() -> None:
    labels = [Label(**lb) for lb in _load("labels.json")]
    units = sum(lb.value for lb in labels if lb.field == "passing_rent")
    total = next(lb.value for lb in labels if lb.field == "total_rent")
    assert units == total == 110800


def test_planted_errors_present() -> None:
    v7 = {(f["unit_id"], f["field"]): f for f in _load("facts_v7.json")}
    grok = {(f["unit_id"], f["field"]): f for f in _load("facts_grok.json")}
    assert v7[("Unit 3", "passing_rent")]["value"] != grok[("Unit 3", "passing_rent")]["value"]
    assert ("Unit 1", "break_date") in v7 and ("Unit 1", "break_date") not in grok
    pages = json.loads((FIXTURES / "pages_hidings.json").read_text())["pages"]
    page3 = " ".join(pages[2]["text"].split())
    assert " ".join(grok[("Unit 2", "break_date")]["quote"].split()) not in page3


def test_crn_keeps_leading_zero() -> None:
    labels = {(lb["unit_id"], lb["field"]): lb for lb in _load("labels.json")}
    assert labels[("Unit 2", "tenant_crn")]["value"] == "00526923"
