import json
from datetime import date
from pathlib import Path

from core.models import Fact
from verify import verify_doc
from verify.grounding import ground
from verify.routing import route
from verify.units import match_units, normalize_unit_id, values_equal

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
PAGES = [p["text"] for p in json.loads((FIXTURES / "pages_hidings.json").read_text())["pages"]]
VALUATION = date(2026, 11, 12)


def _load(name: str) -> list[Fact]:
    return [Fact(**f) for f in json.loads((FIXTURES / name).read_text())]


def _index(facts: list[Fact]) -> dict[tuple[str, str | None, str], Fact]:
    return {(f.extractor, f.unit_id, f.field): f for f in facts}


def _verified() -> dict[tuple[str, str | None, str], Fact]:
    return _index(verify_doc({"v7go": _load("facts_v7.json"), "grok": _load("facts_grok.json")},
                             PAGES, None, VALUATION))


def test_normalize_unit_id() -> None:
    assert normalize_unit_id("Unit 1") == "1"
    assert normalize_unit_id("  UNIT  01 ") == "1"
    assert normalize_unit_id("Ground Floor, Unit 2A") == "ground floor 2a"
    assert normalize_unit_id(None) == ""


def test_match_units() -> None:
    m = match_units(["Unit 1", "Unit 2", None, "Unit 3 Ground Floor"], ["unit 2", "Unit 01", None, "Unit 3 Ground Flr"])
    assert m == {None: None, "Unit 1": "Unit 01", "Unit 2": "unit 2", "Unit 3 Ground Floor": "Unit 3 Ground Flr"}
    assert match_units(["Unit 1 Ground"], ["Unit 2 Ground"]) == {}


def test_values_equal() -> None:
    assert values_equal("passing_rent", 44500, "£44,500")
    assert not values_equal("passing_rent", 25500, 20000)
    assert values_equal("area_sqft", 4142, 4170)
    assert not values_equal("area_sqft", 4142, 4200)
    assert values_equal("break_date", "2027-06-01", "01/06/2027")
    assert not values_equal("break_date", "2027-06-01", "2027-01-06")
    assert values_equal("tenant", "Howden Joinery Limited", "HOWDEN JOINERY LIMITED")
    assert not values_equal("tenant", "Howden Joinery Limited", "Burkert UK Limited")
    assert values_equal("tenant_crn", "00526923", "00526923")
    assert not values_equal("tenant_crn", "00526923", "526923")
    assert values_equal("lease_term_years", 10, 10.0)
    assert values_equal("tenant", None, None)
    assert not values_equal("tenant", "x", None)


def test_disagreement_routes_to_review() -> None:
    out = _verified()
    for ex in ("v7go", "grok"):
        f = out[(ex, "Unit 3", "passing_rent")]
        assert (f.review_status, f.review_reason) == ("review", "extractors_disagree")


def test_missed_footnote_break_is_missing_in_one() -> None:
    f = _verified()[("v7go", "Unit 1", "break_date")]
    assert f.grounded and f.date_check == "ok"
    assert (f.review_status, f.review_reason) == ("review", "missing_in_one")


def test_invented_break_is_ungrounded() -> None:
    f = _verified()[("grok", "Unit 2", "break_date")]
    assert f.grounded is False
    assert (f.review_status, f.review_reason) == ("review", "ungrounded")


def test_reversion_mismatch_is_date_inconsistent() -> None:
    out = _verified()
    for ex in ("v7go", "grok"):
        f = out[(ex, "Unit 2", "lease_expiry")]
        assert (f.review_status, f.review_reason) == ("review", "date_inconsistent")


def test_matching_fields_are_auto() -> None:
    out = _verified()
    for key in [("Unit 1", "passing_rent"), ("Unit 1", "lease_expiry"), ("Unit 2", "tenant_crn"),
                (None, "total_rent"), ("Unit 4", "break_date"), ("Unit 1", "area_sqft")]:
        for ex in ("v7go", "grok"):
            f = out[(ex, *key)]
            assert (f.review_status, f.review_reason) == ("auto", None), (ex, key)


def test_verify_doc_counts() -> None:
    facts = list(_verified().values())
    assert len(facts) == 72
    review = sorted((f.extractor, f.unit_id, f.field) for f in facts if f.review_status == "review")
    assert review == [
        ("grok", "Unit 2", "break_date"),
        ("grok", "Unit 2", "lease_expiry"),
        ("grok", "Unit 3", "passing_rent"),
        ("v7go", "Unit 1", "break_date"),
        ("v7go", "Unit 2", "lease_expiry"),
        ("v7go", "Unit 3", "passing_rent"),
    ]


def test_route_number_not_in_quote() -> None:
    a = ground(Fact(doc_id="d", unit_id="Unit 1", field="passing_rent", value=45000, quote="£44,500", page=3,
                    extractor="v7go", author="t"), PAGES)
    b = a.model_copy(update={"extractor": "grok"})
    out = route([a], [b])
    assert {(f.review_status, f.review_reason) for f in out} == {("review", "number_not_in_quote")}


def test_route_ungrounded_when_not_checked() -> None:
    a = Fact(doc_id="d", unit_id="Unit 1", field="passing_rent", value=44500, quote="£44,500", page=3,
             extractor="v7go", author="t")
    out = route([a], [a.model_copy(update={"extractor": "grok"})])
    assert all(f.review_reason == "ungrounded" for f in out)


def test_route_pairs_units_by_fuzzy_id_and_does_not_mutate() -> None:
    a = ground(Fact(doc_id="d", unit_id="Unit 1", field="passing_rent", value=44500, quote="£44,500", page=3,
                    extractor="v7go", author="t"), PAGES)
    b = a.model_copy(update={"extractor": "grok", "unit_id": "UNIT 01"})
    out = route([a], [b])
    assert len(out) == 2 and all(f.review_status == "auto" for f in out)
    assert a.review_status is None
