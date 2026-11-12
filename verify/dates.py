"""UK date parsing and per-unit date consistency checks. Pure functions, no network, no DB."""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta

from core.models import DATE_FIELDS, DateCheck, Fact, valuation_date

_MONTHS: dict[str, int] = {}
for _i, _name in enumerate(calendar.month_name):
    if _i:
        _MONTHS[_name.lower()] = _i
        _MONTHS[_name[:3].lower()] = _i
_MONTHS["sept"] = 9

_MONTH_RE = r"(?P<month>" + "|".join(sorted(_MONTHS, key=len, reverse=True)) + r")\.?"

_ISO_RE = re.compile(r"\b(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})\b")
# Day first, always: 05/06/2022 is 5 June 2022.
_NUMERIC_RE = re.compile(r"(?<![\d/.\-])(?P<d>\d{1,2})[/.\-](?P<m>\d{1,2})[/.\-](?P<y>\d{4}|\d{2})(?![\d/.\-]*\d)")
_TEXT_RE = re.compile(
    r"\b(?:the\s+)?(?P<d>\d{1,2})(?:st|nd|rd|th)?(?:\s+day)?(?:\s+of)?\s+" + _MONTH_RE + r",?\s+(?P<y>\d{4})\b",
    re.IGNORECASE,
)
_MONTH_YEAR_RE = re.compile(r"\b" + _MONTH_RE + r",?\s+(?P<y>\d{4})\b", re.IGNORECASE)

_TERM_RE = re.compile(
    r"(?P<years>\d+(?:\.\d+)?)\s*(?:years?|yrs?)"
    r"(?:\s*(?:and|&|,)?\s*(?P<months>\d+)\s*months?)?"
    r"\s+from(?:\s+and\s+including)?\s+(?P<rest>.+)",
    re.IGNORECASE | re.DOTALL,
)
_MONTHS_TERM_RE = re.compile(
    r"(?P<months>\d+)\s*months?\s+from(?:\s+and\s+including)?\s+(?P<rest>.+)", re.IGNORECASE | re.DOTALL
)

APPROX_TOLERANCE_DAYS = 366


def _safe_date(y: int, m: int, d: int) -> date | None:
    if y < 100:
        y += 2000
    try:
        return date(y, m, d)
    except ValueError:
        return None


def find_dates(s: str | None) -> list[date]:
    """All UK dates in the text, in order of appearance. 'April 2027' becomes 2027-04-01."""
    if not s:
        return []
    found: list[tuple[int, date]] = []
    taken: list[tuple[int, int]] = []

    def free(span: tuple[int, int]) -> bool:
        return all(span[1] <= a or span[0] >= b for a, b in taken)

    for rx, kind in ((_ISO_RE, "iso"), (_NUMERIC_RE, "num"), (_TEXT_RE, "text"), (_MONTH_YEAR_RE, "my")):
        for m in rx.finditer(s):
            if not free(m.span()):
                continue
            if kind in ("iso", "num"):
                d = _safe_date(int(m["y"]), int(m["m"]), int(m["d"]))
            elif kind == "text":
                d = _safe_date(int(m["y"]), _MONTHS[m["month"].lower()], int(m["d"]))
            else:
                d = _safe_date(int(m["y"]), _MONTHS[m["month"].lower()], 1)
            if d:
                found.append((m.start(), d))
                taken.append(m.span())
    return [d for _, d in sorted(found, key=lambda x: x[0])]


def parse_uk_date(s: str | None) -> date | None:
    """First UK date in the string (day first only), or None."""
    if not s:
        return None
    s = s.strip()
    try:
        return date.fromisoformat(s)
    except ValueError:
        pass
    dates = find_dates(s)
    return dates[0] if dates else None


def parse_term(s: str | None) -> tuple[float, date] | None:
    """'10 years from 01/06/2022' -> (10.0, 2022-06-01). Months become fractions of a year."""
    if not s:
        return None
    m = _TERM_RE.search(s)
    if m:
        start = parse_uk_date(m["rest"])
        if start is None:
            return None
        years = float(m["years"]) + (int(m["months"]) / 12 if m["months"] else 0.0)
        return years, start
    m = _MONTHS_TERM_RE.search(s)
    if m:
        start = parse_uk_date(m["rest"])
        return (int(m["months"]) / 12, start) if start else None
    return None


def add_months(d: date, months: int) -> date:
    y, mo = divmod(d.month - 1 + months, 12)
    year, month = d.year + y, mo + 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def expected_expiry(start: date, years: float) -> date:
    """Lease of N years from start ends the day before the Nth anniversary."""
    return add_months(start, round(years * 12)) - timedelta(days=1)


def _to_iso(value: object) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str):
        d = parse_uk_date(value)
        return d.isoformat() if d else None
    return None


def normalize_date_facts(facts: list[Fact]) -> list[Fact]:
    """Copies with date values converted to ISO; unparseable values are left as they are."""
    out: list[Fact] = []
    for f in facts:
        if f.field in DATE_FIELDS and f.value is not None:
            iso = _to_iso(f.value)
            out.append(f.model_copy(update={"value": iso}) if iso else f.model_copy())
        else:
            out.append(f.model_copy())
    return out


def _to_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        m = re.search(r"\d+(?:\.\d+)?", value)
        return float(m.group()) if m else None
    return None


def _first_by_field(unit_facts: list[Fact]) -> dict[str, Fact]:
    out: dict[str, Fact] = {}
    for f in unit_facts:
        out.setdefault(f.field, f)
    return out


def _is_approx(facts: dict[str, Fact]) -> bool:
    for name in ("lease_term_years", "lease_start"):
        f = facts.get(name)
        if f and f.quote and "approx" in f.quote.lower():
            return True
    return False


def check_unit(unit_facts: list[Fact], valuation: date | None = None) -> dict[str, DateCheck]:
    """Date consistency per field for one unit of one extractor.

    expiry vs start + term (±1 day, ±1 year when the term is 'Approx.'), start < break < expiry,
    start <= rent review <= expiry. Non-date fields are 'not_applicable'. `valuation` is accepted for
    signature symmetry with timeline_flags; passed breaks are not inconsistent.
    """
    by_field = _first_by_field(unit_facts)
    result: dict[str, DateCheck] = {}
    dates: dict[str, date] = {}
    for name, f in by_field.items():
        if name in DATE_FIELDS:
            d = parse_uk_date(f.value) if isinstance(f.value, str) else None
            if d is None:
                result[name] = "inconsistent"
            else:
                dates[name] = d
                result[name] = "ok"
        elif name == "lease_term_years":
            result[name] = "ok" if _to_float(f.value) is not None else "inconsistent"
        else:
            result[name] = "not_applicable"

    start, expiry = dates.get("lease_start"), dates.get("lease_expiry")
    brk, review = dates.get("break_date"), dates.get("rent_review_date")
    term = _to_float(by_field["lease_term_years"].value) if "lease_term_years" in by_field else None

    if start and expiry and term:
        tol = APPROX_TOLERANCE_DAYS if _is_approx(by_field) else 1
        if abs((expected_expiry(start, term) - expiry).days) > tol:
            result["lease_expiry"] = "inconsistent"
    if start and expiry and start >= expiry:
        result["lease_start"] = result["lease_expiry"] = "inconsistent"
    if brk and ((start and brk <= start) or (expiry and brk >= expiry)):
        result["break_date"] = "inconsistent"
    if review and ((start and review < start) or (expiry and review > expiry)):
        result["rent_review_date"] = "inconsistent"
    return result


def timeline_flags(unit_facts: list[Fact], valuation: date | None = None) -> dict[str, bool]:
    valuation = valuation or valuation_date()
    by_field = _first_by_field(unit_facts)

    def get(name: str) -> date | None:
        f = by_field.get(name)
        return parse_uk_date(f.value) if f and isinstance(f.value, str) else None

    brk, expiry = get("break_date"), get("lease_expiry")
    return {
        "break_passed": bool(brk and brk < valuation),
        "expired": bool(expiry and expiry < valuation),
    }
