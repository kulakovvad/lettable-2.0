import json
from pathlib import Path

import pytest

from core.models import Fact
from verify.grounding import ground, normalize_text, number_in_quote, quote_score

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
PAGES = [p["text"] for p in json.loads((FIXTURES / "pages_hidings.json").read_text())["pages"]]


def _facts(name: str) -> dict[tuple[str | None, str], Fact]:
    return {(f["unit_id"], f["field"]): Fact(**f) for f in json.loads((FIXTURES / name).read_text())}


def _f(field: str, value: object, quote: str | None, page: int | None = 3) -> Fact:
    return Fact(doc_id="d", unit_id="Unit 1", field=field, value=value, quote=quote, page=page,
                extractor="grok", author="t")


def test_normalize_text() -> None:
    assert normalize_text("£110,800  per\nAnnum") == "110800 per annum"
    assert normalize_text("\ufb01nal \ufb02oor") == "final floor"
    assert normalize_text("tenant\u2019s op-\ntion") == "tenant's option"
    assert normalize_text(None) == ""


def test_quote_score() -> None:
    assert quote_score("£44,500", PAGES[2]) == 100.0
    assert quote_score("SOUTH WESTERN  SPECIALISED\nTAPE LIMITED", PAGES[2]) == 100.0
    assert quote_score("completely unrelated sentence about a swimming pool", PAGES[2]) < 90


def test_all_v7_facts_ground() -> None:
    for f in _facts("facts_v7.json").values():
        g = ground(f, PAGES)
        assert g.grounded, (f.unit_id, f.field)
        assert g.page == f.page


def test_invented_quote_is_ungrounded() -> None:
    invented = _facts("facts_grok.json")[("Unit 2", "break_date")]
    assert quote_score(invented.quote, PAGES[2]) >= 90  # fuzzy alone would pass it
    g = ground(invented, PAGES)
    assert g.grounded is False


def test_near_miss_short_quote_is_ungrounded() -> None:
    assert ground(_f("passing_rent", 22500, "£22,500"), PAGES).grounded is False


def test_quote_inside_a_longer_number_is_ungrounded() -> None:
    assert ground(_f("passing_rent", 4500, "£4,500"), PAGES).grounded is False
    assert ground(_f("passing_rent", 2300, "2,300"), PAGES).grounded is False


def test_thousands_comma_before_letters() -> None:
    assert normalize_text("£25,500per annum") == "25500per annum"
    assert number_in_quote(_f("passing_rent", 25500, "rent reserved of £25,500per annum"))


def test_single_page_string_keeps_page() -> None:
    assert ground(_f("passing_rent", 44500, "£44,500", page=None), PAGES[2]).page is None


def test_bbox_dropped_when_quote_moves_page() -> None:
    f = _f("total_rent", 110800, "£110,800 per Annum Exclusive", page=3).model_copy(update={"bbox": [1.0, 2, 3, 4]})
    g = ground(f, PAGES)
    assert g.page == 2 and g.bbox is None


def test_ground_finds_quote_on_other_page() -> None:
    g = ground(_f("total_rent", 110800, "£110,800 per Annum Exclusive", page=3), PAGES)
    assert g.grounded and g.page == 2


def test_ground_without_quote() -> None:
    assert ground(_f("passing_rent", 44500, None), PAGES).grounded is False
    assert ground(_f("passing_rent", 44500, "   "), PAGES).grounded is False


def test_ground_sets_confidence_only_when_missing() -> None:
    grok = _facts("facts_grok.json")[("Unit 1", "passing_rent")]
    assert grok.confidence is None
    assert ground(grok, PAGES).confidence == 1.0
    v7 = _facts("facts_v7.json")[("Unit 1", "passing_rent")]
    assert ground(v7, PAGES).confidence == 0.9


def test_ground_returns_copy() -> None:
    f = _f("passing_rent", 44500, "£44,500")
    ground(f, PAGES)
    assert f.grounded is None


def test_ground_single_page_string() -> None:
    assert ground(_f("passing_rent", 44500, "£44,500"), PAGES[2]).grounded is True


def test_ground_bbox_from_pdf() -> None:
    pdf = Path("data/pdfs/acuitus-90f42467_unit-1-to-4-the-hidings-cirencester-gloucestershir-13c9c3.pdf")
    if not pdf.exists():
        pytest.skip("brochure PDF not available")
    g = ground(_facts("facts_v7.json")[("Unit 1", "passing_rent")], PAGES, pdf)
    assert g.bbox and len(g.bbox) == 4


def test_ground_bbox_missing_pdf_does_not_fail() -> None:
    g = ground(_f("passing_rent", 44500, "£44,500"), PAGES, "/nonexistent.pdf")
    assert g.grounded and g.bbox is None


@pytest.mark.parametrize(
    ("field", "value", "quote", "expected"),
    [
        ("passing_rent", 44500, "£44,500", True),
        ("passing_rent", 25500, "£20,000 rising to £25,500 in April 2027", True),
        ("passing_rent", 45000, "£44,500", False),
        ("area_sqft", 4142, "(2,818)\n(1,324)", True),
        ("area_sqft", 1562, "(1,562)", True),
        ("lease_term_years", 17, "Approx. 17 years from", True),
        ("lease_term_years", 10.5, "10 years and 6 months from 01/06/2022", True),
        ("lease_start", "2022-06-01", "10 years from 01/06/2022", True),
        ("break_date", "2027-06-01", "option to determine the\nlease on 01/06/2027.", True),
        ("break_date", "2027-01-06", "option to determine the lease on 01/06/2027.", False),
        ("tenant", "South Western Specialised Tape Limited", "SOUTH WESTERN SPECIALISED TAPE LIMITED", True),
        ("tenant", "Howden Joinery Limited", "BURKERT UK LIMITED", False),
        ("tenant_crn", "00526923", "00526923", True),
        ("tenant_crn", "526923", "00526923", False),
        ("tenant_crn", "02989512", "SOUTH WESTERN SPECIALISED TAPE LIMITED (CRN\n02989512)", True),
        ("property_type", "Industrial", "Well Located Freehold Industrial Investment", True),
        ("passing_rent", None, "£44,500", False),
        ("passing_rent", 44500, None, False),
    ],
)
def test_number_in_quote(field: str, value: object, quote: str | None, expected: bool) -> None:
    assert number_in_quote(_f(field, value, quote)) is expected
