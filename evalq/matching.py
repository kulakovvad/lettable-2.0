"""Unit matching and value comparison shared by the proforma and the evaluation.

Uses Agent 3's implementations from `verify` when they are importable, otherwise local
fallbacks with the same signatures.
"""

from __future__ import annotations

import re
from typing import Any

from rapidfuzz import fuzz

from core.models import DATE_FIELDS


def _normalize_unit_id(s: str | None) -> str | None:
    if s is None:
        return None
    t = re.sub(r"\s+", " ", str(s)).strip().lower()
    t = re.sub(r"\b(no\.?|number)\s*", "", t)
    t = re.sub(r"^(unit|units)\s*", "unit ", t)
    return t.strip()


def _match_units(ids_a: list[str], ids_b: list[str]) -> dict[str, str]:
    """Map each id in ids_a to an id in ids_b: exact after normalization, else fuzzy >= 85."""
    norm_b = {_normalize_unit_id(b): b for b in ids_b}
    out: dict[str, str] = {}
    used: set[str] = set()
    for a in ids_a:
        na = _normalize_unit_id(a)
        if na in norm_b:
            out[a] = norm_b[na]
            used.add(norm_b[na])
    for a in ids_a:
        if a in out:
            continue
        na = _normalize_unit_id(a) or ""
        best, best_score = None, 0.0
        for nb, b in norm_b.items():
            if b in used:
                continue
            score = fuzz.ratio(na, nb or "")
            if score > best_score:
                best, best_score = b, score
        if best is not None and best_score >= 85:
            out[a] = best
            used.add(best)
    return out


def _to_number(v: Any) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        s = v.replace(",", "").replace("£", "").strip()
        try:
            return float(s)
        except ValueError:
            return None
    return None


def _values_equal(field: str, a: Any, b: Any) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if field in ("passing_rent", "total_rent"):
        na, nb = _to_number(a), _to_number(b)
        return na is not None and na == nb
    if field in ("area_sqft", "total_area_sqft"):
        na, nb = _to_number(a), _to_number(b)
        if na is None or nb is None:
            return False
        return abs(na - nb) <= 0.01 * max(abs(na), abs(nb), 1.0)
    if field in ("lease_term_years", "niy_pct"):
        na, nb = _to_number(a), _to_number(b)
        return na is not None and nb is not None and abs(na - nb) < 0.05
    if field in DATE_FIELDS or field == "tenant_crn":
        return str(a).strip() == str(b).strip()
    return fuzz.token_set_ratio(str(a).lower(), str(b).lower()) >= 90


try:
    from verify.units import match_units, normalize_unit_id  # type: ignore[no-redef]
except ImportError:
    normalize_unit_id = _normalize_unit_id
    match_units = _match_units

try:
    from verify.units import values_equal  # type: ignore[no-redef]
except ImportError:
    values_equal = _values_equal
