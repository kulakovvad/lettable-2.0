"""Grounding: is the fact's verbatim quote really on the page, and does it contain the value?"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path

from rapidfuzz import fuzz

from core.models import DATE_FIELDS, FIELD_TYPES, Fact
from core.pdf import find_bbox
from verify.dates import find_dates, parse_term, parse_uk_date

GROUND_THRESHOLD = 90.0
STRING_THRESHOLD = 85.0
# Below this length a fuzzy hit is too easy (e.g. "£22,500" vs "£22,300"), so require an exact substring.
MIN_FUZZY_LEN = 20
AREA_FIELDS = frozenset({"area_sqft", "total_area_sqft"})

_QUOTES = str.maketrans({"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"', "\u2013": "-",
                         "\u2014": "-", "\u00a0": " ", "\u00ad": ""})
_HYPHEN_BREAK = re.compile(r"(\w)-\s*\n\s*(\w)")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
_WS = re.compile(r"\s+")
_NUM_TOKEN = re.compile(r"\d+(?:[./]\d+)*")
_NUMBER = re.compile(r"\d+(?:\.\d+)?")


def normalize_text(s: str | None) -> str:
    """Lowercase, NFKC (ligatures), straight quotes, joined hyphen breaks, no '£' or thousands commas."""
    if not s:
        return ""
    s = unicodedata.normalize("NFKC", s).translate(_QUOTES)
    s = _HYPHEN_BREAK.sub(r"\1\2", s)
    s = s.replace("£", "")
    s = _THOUSANDS.sub("", s)
    return _WS.sub(" ", s).strip().lower()


def _locate(q: str, p: str) -> tuple[float, bool]:
    """(score, matched) for normalized quote q in normalized page p."""
    if not q or not p:
        return 0.0, False
    if q in p:
        return 100.0, True
    aln = fuzz.partial_ratio_alignment(q, p)
    if aln is None:
        return 0.0, False
    score = float(aln.score)
    if len(q) < MIN_FUZZY_LEN or score < GROUND_THRESHOLD:
        return score, False
    # A near-verbatim sentence with a different date or amount is the typical invented quote.
    window = p[max(0, aln.dest_start - 5): aln.dest_end + 5]
    return score, all(tok in window for tok in _NUM_TOKEN.findall(q))


def quote_score(quote: str | None, page_text: str | None) -> float:
    """0-100 similarity of the quote to its best-matching span of the page."""
    return _locate(normalize_text(quote), normalize_text(page_text))[0]


def _numbers(s: str) -> list[float]:
    return [float(x) for x in _NUMBER.findall(normalize_text(s))]


def _as_number(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        nums = _numbers(value)
        return nums[0] if nums else None
    return None


def number_in_quote(fact: Fact) -> bool:
    """The value can be read off the quote itself (not just somewhere on the page)."""
    if fact.value is None or not fact.quote:
        return False
    quote = fact.quote
    if fact.field in DATE_FIELDS:
        target = parse_uk_date(str(fact.value))
        return target is not None and target in find_dates(quote)
    if fact.field == "tenant_crn":
        return str(fact.value).strip().upper() in re.findall(r"[A-Z0-9]+", quote.upper())
    kind = FIELD_TYPES.get(fact.field, str)
    if kind in (int, float):
        target = _as_number(fact.value)
        if target is None:
            return False
        nums = _numbers(quote)
        if any(abs(n - target) < 0.01 for n in nums):
            return True
        if fact.field in AREA_FIELDS and len(nums) > 1 and abs(sum(nums) - target) < 0.01:
            return True
        if fact.field == "lease_term_years":
            term = parse_term(quote)
            return term is not None and abs(term[0] - target) < 0.01
        return False
    return fuzz.token_set_ratio(normalize_text(str(fact.value)), normalize_text(quote)) >= STRING_THRESHOLD


def _page_order(page: int | None, n: int) -> list[int]:
    pages = list(range(1, n + 1))
    if page and 1 <= page <= n:
        pages.remove(page)
        pages.insert(0, page)
    return pages


def ground(fact: Fact, page_texts: list[str] | str, pdf_path: str | Path | None = None) -> Fact:
    """Copy of the fact with `grounded`, the page where the quote was found, bbox and confidence.

    Searches the stated page first, then every other page. `page_texts[i]` is page i + 1; a single
    string is treated as the text of the stated page.
    """
    texts = [page_texts] if isinstance(page_texts, str) else list(page_texts)
    single = isinstance(page_texts, str)
    q = normalize_text(fact.quote)
    update: dict[str, object] = {"grounded": False}
    best = 0.0
    if q:
        order = [fact.page or 1] if single else _page_order(fact.page, len(texts))
        for page in order:
            text = texts[0] if single else texts[page - 1]
            score, ok = _locate(q, normalize_text(text))
            best = max(best, score)
            if ok:
                update.update(grounded=True, page=page)
                if pdf_path:
                    update["bbox"] = _safe_bbox(pdf_path, page, fact.quote or "") or fact.bbox
                break
    if fact.confidence is None:
        update["confidence"] = round(best / 100, 4)
    return fact.model_copy(update=update)


def _safe_bbox(pdf_path: str | Path, page: int, quote: str) -> list[float] | None:
    try:
        return find_bbox(pdf_path, page, quote)
    except Exception:  # bbox is cosmetic; a damaged or missing PDF must not fail verification
        return None
