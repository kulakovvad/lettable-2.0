"""Unit-id matching and field-aware value comparison, shared by routing and evaluation."""

from __future__ import annotations

import re

from rapidfuzz import fuzz

from core.models import DATE_FIELDS, FIELD_TYPES, Value
from verify.dates import parse_uk_date
from verify.grounding import normalize_text

UNIT_MATCH_THRESHOLD = 85.0
STRING_EQUAL_THRESHOLD = 90.0
AREA_TOLERANCE = 0.01
_NOISE_TOKENS = frozenset({"unit", "units", "no", "number"})


def normalize_unit_id(s: str | None) -> str:
    """'Unit 01' -> '1', 'Ground Floor, Unit 2A' -> 'ground floor 2a'."""
    if not s:
        return ""
    s = re.sub(r"[^a-z0-9 ]", " ", normalize_text(s).replace("&", " and "))
    tokens = [t.lstrip("0") or "0" if t.isdigit() else t for t in s.split() if t not in _NOISE_TOKENS]
    return " ".join(tokens)


def _digits(s: str) -> list[str]:
    return re.findall(r"\d+", s)


def match_units(ids_a: list[str | None], ids_b: list[str | None]) -> dict[str | None, str | None]:
    """One-to-one map from ids_a to ids_b: exact after normalization, else fuzzy >= 85.

    Numeric tokens must agree exactly, so 'Unit 1' never pairs with 'Unit 2'. Unmatched ids are absent.
    """
    out: dict[str | None, str | None] = {}
    a_left = list(dict.fromkeys(ids_a))
    b_left = list(dict.fromkeys(ids_b))
    if None in a_left and None in b_left:
        out[None] = None
        a_left.remove(None)
        b_left.remove(None)
    a_ids = [a for a in a_left if a is not None]
    b_ids = [b for b in b_left if b is not None]

    norm_b = {b: normalize_unit_id(b) for b in b_ids}
    for a in list(a_ids):
        na = normalize_unit_id(a)
        hit = next((b for b in b_ids if norm_b[b] == na), None)
        if hit is not None:
            out[a] = hit
            a_ids.remove(a)
            b_ids.remove(hit)

    candidates: list[tuple[float, str, str]] = []
    for a in a_ids:
        na = normalize_unit_id(a)
        for b in b_ids:
            nb = norm_b[b]
            if _digits(na) != _digits(nb):
                continue
            score = float(fuzz.ratio(na, nb))
            if score >= UNIT_MATCH_THRESHOLD:
                candidates.append((score, a, b))
    used_a: set[str] = set()
    used_b: set[str] = set()
    for _, a, b in sorted(candidates, reverse=True):
        if a not in used_a and b not in used_b:
            out[a] = b
            used_a.add(a)
            used_b.add(b)
    return out


_SUFFIXES = {"ltd": "limited", "co": "company", "&": "and"}


def _entity(s: str) -> str:
    tokens = re.sub(r"[^a-z0-9& ]", " ", normalize_text(s)).split()
    return " ".join(_SUFFIXES.get(t, t) for t in tokens)


def _num(v: Value) -> float | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    if isinstance(v, str):
        m = re.search(r"\d+(?:\.\d+)?", normalize_text(v))
        return float(m.group()) if m else None
    return None


def values_equal(field: str, a: Value, b: Value) -> bool:
    if a is None or b is None:
        return a is None and b is None
    if field == "tenant_crn":
        return str(a).strip().upper() == str(b).strip().upper()
    if field in DATE_FIELDS:
        da, db = parse_uk_date(str(a)), parse_uk_date(str(b))
        return da is not None and da == db
    kind = FIELD_TYPES.get(field, str)
    if kind in (int, float):
        na, nb = _num(a), _num(b)
        if na is None or nb is None:
            return False
        if field in ("area_sqft", "total_area_sqft"):
            return abs(na - nb) <= AREA_TOLERANCE * max(abs(na), abs(nb))
        if kind is int:
            return round(na) == round(nb)
        return abs(na - nb) < 0.01
    # token_sort, not token_set: a subset must not count as equal ("Howden Joinery Limited" vs
    # "Howden Joinery Properties Limited" are different companies).
    return fuzz.token_sort_ratio(_entity(str(a)), _entity(str(b))) >= STRING_EQUAL_THRESHOLD
