from datetime import date

from core.models import Fact
from verify.dates import (
    check_unit,
    expected_expiry,
    find_dates,
    is_future_step_rent,
    normalize_date_facts,
    parse_term,
    parse_uk_date,
    timeline_flags,
)

VALUATION = date(2026, 11, 12)


def _f(field: str, value: object, quote: str | None = None, unit: str = "Unit 1") -> Fact:
    return Fact(doc_id="d", unit_id=unit, field=field, value=value, quote=quote, page=3,
                extractor="v7go", author="t")


def test_parse_uk_date_is_day_first() -> None:
    assert parse_uk_date("05/06/2022") == date(2022, 6, 5)
    assert parse_uk_date("01/06/2022") == date(2022, 6, 1)
    assert parse_uk_date("13/01/2024") == date(2024, 1, 13)


def test_parse_uk_date_text_formats() -> None:
    assert parse_uk_date("1 June 2022") == date(2022, 6, 1)
    assert parse_uk_date("1st June 2022") == date(2022, 6, 1)
    assert parse_uk_date("the 25th day of March 2024") == date(2024, 3, 25)
    assert parse_uk_date("18th November\n2020") == date(2020, 11, 18)
    assert parse_uk_date("April 2027") == date(2027, 4, 1)
    assert parse_uk_date("2022-06-01") == date(2022, 6, 1)


def test_parse_uk_date_rejects_garbage() -> None:
    assert parse_uk_date("") is None
    assert parse_uk_date(None) is None
    assert parse_uk_date("31/02/2024") is None
    assert parse_uk_date("£44,500") is None


def test_find_dates_in_quote() -> None:
    q = "The lease provides for a tenant option to determine the\nlease on 01/06/2027."
    assert find_dates(q) == [date(2027, 6, 1)]
    assert find_dates("29/09/2026,\n29/09/2031") == [date(2026, 9, 29), date(2031, 9, 29)]
    assert find_dates("from 1st April 2027 until 31st March 2037") == [date(2027, 4, 1), date(2037, 3, 31)]
    assert find_dates("01/06/2022-31/05/2032") == [date(2022, 6, 1), date(2032, 5, 31)]
    assert find_dates("1/2/3/2024") == []


def test_future_step_rent() -> None:
    q = "£20,000 rising to £25,500 in April 2027"
    assert is_future_step_rent(_f("passing_rent", 25500, q), VALUATION)
    assert not is_future_step_rent(_f("passing_rent", 20000, q), VALUATION)
    assert not is_future_step_rent(_f("passing_rent", 25500, q), date(2027, 5, 1))
    assert check_unit([_f("passing_rent", 25500, q)], VALUATION)["passing_rent"] == "inconsistent"
    assert check_unit([_f("passing_rent", 20000, q)], VALUATION)["passing_rent"] == "not_applicable"


def test_parse_term_variants() -> None:
    assert parse_term("10 years from 01/06/2022") == (10.0, date(2022, 6, 1))
    assert parse_term("Approx. 17 years from\n18/11/2020") == (17.0, date(2020, 11, 18))
    assert parse_term("5 years from and including 25/03/2024") == (5.0, date(2024, 3, 25))
    years, start = parse_term("10 years and 6 months from 1st June 2022")  # type: ignore[misc]
    assert years == 10.5 and start == date(2022, 6, 1)
    assert parse_term("£44,500") is None


def test_expected_expiry() -> None:
    assert expected_expiry(date(2022, 6, 1), 10) == date(2032, 5, 31)
    assert expected_expiry(date(2026, 8, 24), 6) == date(2032, 8, 23)
    assert expected_expiry(date(2022, 6, 1), 10.5) == date(2032, 11, 30)
    assert expected_expiry(date(2024, 2, 29), 1) == date(2025, 2, 27)


def test_check_unit_hidings_unit1_ok() -> None:
    facts = [
        _f("lease_start", "2022-06-01", "10 years from 01/06/2022"),
        _f("lease_term_years", 10, "10 years from 01/06/2022"),
        _f("lease_expiry", "2032-05-31", "(31/05/2032)"),
        _f("break_date", "2027-06-01"),
        _f("rent_review_date", "2027-03-25"),
        _f("passing_rent", 44500),
    ]
    res = check_unit(facts, VALUATION)
    assert res["lease_expiry"] == "ok"
    assert res["break_date"] == "ok"
    assert res["rent_review_date"] == "ok"
    assert res["passing_rent"] == "not_applicable"


def test_check_unit_hidings_unit2_reversion_mismatch() -> None:
    facts = [
        _f("lease_start", "2026-09-29", "7 years from 29/09/2026", "Unit 2"),
        _f("lease_term_years", 7, "7 years from 29/09/2026", "Unit 2"),
        _f("lease_expiry", "2033-08-31", "(31/08/2033)", "Unit 2"),
    ]
    assert check_unit(facts, VALUATION)["lease_expiry"] == "inconsistent"


def test_check_unit_one_day_tolerance() -> None:
    facts = [_f("lease_start", "2022-06-01"), _f("lease_term_years", 10), _f("lease_expiry", "2032-06-01")]
    assert check_unit(facts)["lease_expiry"] == "ok"


def test_check_unit_approx_term_tolerated() -> None:
    facts = [
        _f("lease_start", "2020-11-18", "Approx. 17 years from\n18/11/2020"),
        _f("lease_term_years", 17, "Approx. 17 years from"),
        _f("lease_expiry", "2037-03-31", "(31/03/2037)"),
    ]
    assert check_unit(facts)["lease_expiry"] == "ok"


def test_check_unit_order_violations() -> None:
    facts = [
        _f("lease_start", "2022-06-01"),
        _f("lease_expiry", "2032-05-31"),
        _f("break_date", "2033-01-01"),
        _f("rent_review_date", "2021-01-01"),
    ]
    res = check_unit(facts)
    assert res["break_date"] == "inconsistent"
    assert res["rent_review_date"] == "inconsistent"
    assert check_unit([_f("break_date", "not a date")])["break_date"] == "inconsistent"


def test_timeline_flags() -> None:
    assert timeline_flags([_f("break_date", "2027-06-01"), _f("lease_expiry", "2032-05-31")], VALUATION) == {
        "break_passed": False, "expired": False}
    assert timeline_flags([_f("break_date", "2025-01-01"), _f("lease_expiry", "2026-01-01")], VALUATION) == {
        "break_passed": True, "expired": True}


def test_normalize_date_facts_to_iso_without_mutation() -> None:
    src = [_f("break_date", "01/06/2027"), _f("lease_expiry", "31st May 2032"), _f("passing_rent", 44500)]
    out = normalize_date_facts(src)
    assert [f.value for f in out] == ["2027-06-01", "2032-05-31", 44500]
    assert src[0].value == "01/06/2027"
