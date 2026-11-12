from datetime import date

import pytest

from core.models import Label
from reference import acuitus_parser as ap
from reference import selfcheck

D = "acuitus-test"


def test_doc_id_from_path():
    assert ap.doc_id_from_path("data/pdfs/acuitus-90f42467_unit-1-to-4.pdf") == "acuitus-90f42467"


@pytest.mark.parametrize("text,expected", [
    ("01/06/2022", date(2022, 6, 1)),
    ("1/04/2026", date(2026, 4, 1)),
    ("15th January 2026", date(2026, 1, 15)),
    ("April 2027", date(2027, 4, 1)),
    ("31/02/2026", None),
    ("June", None),
])
def test_parse_date_is_day_first(text, expected):
    assert ap.parse_date(text) == expected


def test_money_rounds_to_nearest_pound():
    assert ap.money("£44,500") == 44500
    assert ap.money("£19,290.76") == 19291


@pytest.mark.parametrize("raw,expected", [
    ("SOUTH WESTERN SPECIALISED TAPE LIMITED (CRN\n02989512)\nt/a South Western (2)",
     "South Western Specialised Tape Limited"),
    ("BURKERT UK LIMITED (CRN 00831741)", "Burkert UK Limited"),
    ("REGISTERED OFFICE (UK) LIMITED", "Registered Office (UK) Limited"),
    ("OCTAVIA HOUSING (t/a Octavia Foundation) (2)", "Octavia Housing"),
    ("INDIVIDUAL t/a Phone Shop", "Individual"),
])
def test_tenant_name(raw, expected):
    assert ap.tenant_name(raw) == expected


def test_stepped_rent_uses_amount_at_valuation_date():
    text = "£20,000 rising to £25,500 in April 2027"
    assert ap._rent(text, date(2026, 11, 12)) == (20000, text)
    assert ap._rent(text, date(2027, 5, 1))[0] == 25500


def test_peppercorn_rent_is_zero():
    assert ap._rent("peppercorn", date(2026, 11, 12))[0] == 0


def test_areas_skip_footnote_markers():
    assert ap._areas("(9,833) (2)") == (9833, "(9,833)")
    assert ap._areas("(2,818)\n(1,324)")[0] == 4142
    assert ap._areas("-") == (None, "")


def test_break_from_footnote_picks_first_future_date():
    note = ("As to Unit 4, the lease provides for a tenant option to determine the lease on "
            "24/08/2028 and again on 24/08/2030.")
    when, quote = ap._break_from_text(note, date(2026, 11, 12))
    assert when == date(2028, 8, 24)
    assert quote == "the lease provides for a tenant option to determine the lease on 24/08/2028"


def test_landlord_only_break_is_ignored():
    note = "The lease provides for a landlord option to determine the lease on 01/01/2028."
    assert ap._break_from_text(note, date(2026, 11, 12)) is None


def test_break_labels_bind_by_as_to_and_marker():
    rows = [ap.Row("Unit 1", {}, {"2"}), ap.Row("Unit 2", {}, {"3"})]
    table = ap.Table(page=3, roles=[], rows=rows, total=None)
    text = ("(2) As to Unit 1, the tenant is strong. The lease provides for a tenant option to "
            "determine the lease on 01/06/2027.\n"
            "(3) The tenant has a break option on 29/09/2029.\n")
    got = {lb.unit_id: lb.value for lb in ap.break_labels(D, table, text, date(2026, 11, 12))}
    assert got == {"Unit 1": "2027-06-01", "Unit 2": "2029-09-29"}


def _lbl(unit, field, value):
    return Label(doc_id=D, unit_id=unit, field=field, value=value, page=3, quote=str(value))


def test_selfcheck_agrees_when_totals_match():
    labels = [_lbl("A", "passing_rent", 100), _lbl("B", "passing_rent", 50),
              _lbl("A", "area_sqft", 1000), _lbl("B", "area_sqft", 500),
              _lbl(None, "total_rent", 150), _lbl(None, "total_area_sqft", 1505)]
    assert selfcheck.reasons(D, labels, listing={}) == []
    assert {lb.status for lb in selfcheck.check(D, labels)} == {"agreed"}


def test_selfcheck_disputes_rent_mismatch_and_missing_rent():
    labels = [_lbl("A", "passing_rent", 100), _lbl("B", "tenant", "X"),
              _lbl(None, "total_rent", 150)]
    why = selfcheck.reasons(D, labels, listing={})
    assert any("missing for B" in r for r in why)
    assert any("!= total_rent 150" in r for r in why)
    assert {lb.status for lb in selfcheck.check(D, labels)} == {"disputed"}


def test_selfcheck_single_unit_falls_back_to_listing_rent():
    labels = [_lbl("Whole", "passing_rent", 40000)]
    assert selfcheck.reasons(D, labels, listing={"price_gbp": "40000", "price_basis": "pa"}) == []
    assert selfcheck.reasons(D, labels, listing={"price_gbp": "40000", "price_basis": "guide"})


def test_listing_warnings_do_not_change_status():
    labels = [_lbl("A", "passing_rent", 100), _lbl(None, "total_rent", 100)]
    listing = {"price_gbp": "999", "price_basis": "pa"}
    assert selfcheck.listing_warnings(D, labels, listing)
    assert selfcheck.reasons(D, labels, listing) == []


def test_empty_labels_are_disputed():
    assert selfcheck.check_status([], D) == ("disputed", ["no tenancy table parsed"])
