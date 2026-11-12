"""Deterministic reference labels from the Acuitus "Tenancy & Accommodation" table.

No model calls: the table is read with pymupdf, every label keeps the page and a verbatim quote.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pymupdf

from core.models import FIELD_TYPES, Label, valuation_date
from core.pdf import page_texts

WHOLE_UNIT = "Whole"

_DATE = r"\d{1,2}/\d{1,2}/\d{4}"
_LONG_DATE = r"\d{1,2}(?:st|nd|rd|th)?\s+[A-Z][a-z]+\s+\d{4}"
_ANY_DATE = rf"(?:{_DATE}|{_LONG_DATE})"
_MONEY = r"£\s?[\d,]+(?:\.\d{1,2})?"

_TERM_RE = re.compile(
    rf"(?:(?:Approx\.?|Approximately)\s+)?(\d+(?:\.\d+)?)\s+years?\s+from\s+({_ANY_DATE})",
    re.IGNORECASE,
)
_UNTIL_RE = re.compile(rf"until\s+({_ANY_DATE})", re.IGNORECASE)
_CRN_RE = re.compile(r"\bcrn\s*:?\s*((?:[A-Z]{2})?\d{5,8})\b", re.IGNORECASE)
_PAREN_NUM_RE = re.compile(r"\(\s*([\d,]+)\s*\)")
_BARE_NUM_RE = re.compile(r"\b\d{1,3}(?:,\d{3})+\b|\b\d+\b")
_STEP_RE = re.compile(
    rf"({_MONEY})\s+rising\s+to\s+({_MONEY})(?:\s*(?:pax|p\.a\.x\.?))?\s+(?:in|on|from)\s+"
    rf"({_DATE}|[A-Z][a-z]+\s+\d{{4}}|{_LONG_DATE})",
    re.IGNORECASE,
)
_BREAK_RE = re.compile(
    r"(?:option\s+to\s+determine|break\s+option|option\s+to\s+break|tenant\s+break)"
    rf"[^.;]*?\b(?:on|at)\s+({_ANY_DATE})((?:\s*(?:,|and|and\s+again)\s*(?:on\s+)?{_ANY_DATE})*)",
    re.IGNORECASE | re.DOTALL,
)
_FOOTNOTE_RE = re.compile(r"(?m)^\((\d{1,2})\)\s+(.+?)(?=^\(\d{1,2}\)\s|\Z)", re.DOTALL)
_MARKER_RE = re.compile(r"\((\d{1,2})\)")

_UNIT_HEADERS = ("address", "unit", "unitno", "demise", "site", "property", "premises", "shop")
_ACRONYMS = {"UK", "PLC", "LLP", "GB", "NHS", "HSBC", "TSB", "UPS", "DHL", "BP", "CIC", "II", "III"}
_MONTHS = {m.lower(): i for i, m in enumerate(
    ["January", "February", "March", "April", "May", "June", "July", "August",
     "September", "October", "November", "December"], start=1)}


@dataclass
class Row:
    unit_id: str
    cells: dict[str, str] = field(default_factory=dict)  # role -> cell text (rows of one unit joined)
    markers: set[str] = field(default_factory=set)


@dataclass
class Table:
    page: int  # 1-based
    roles: list[str | None]
    rows: list[Row]
    total: dict[str, str] | None
    reversion_only: bool = False  # last column is "Reversion" with bare expiry dates, no reviews


def doc_id_from_path(path: str | Path) -> str:
    return Path(path).name.split("_", 1)[0]


def parse_date(text: str) -> date | None:
    """UK dates only: dd/mm/yyyy (day first) or '15th January 2026'."""
    text = " ".join(text.split())
    m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", text)
    if m:
        d, mo, y = map(int, m.groups())
        try:
            return date(y, mo, d)
        except ValueError:
            return None
    m = re.fullmatch(r"(\d{1,2})(?:st|nd|rd|th)?\s+([A-Za-z]+)\s+(\d{4})", text)
    if m and m.group(2).lower() in _MONTHS:
        try:
            return date(int(m.group(3)), _MONTHS[m.group(2).lower()], int(m.group(1)))
        except ValueError:
            return None
    m = re.fullmatch(r"([A-Za-z]+)\s+(\d{4})", text)
    if m and m.group(1).lower() in _MONTHS:
        return date(int(m.group(2)), _MONTHS[m.group(1).lower()], 1)
    return None


def money(text: str) -> int:
    """'£19,290.76' -> 19291 (nearest pound)."""
    amount = float(re.sub(r"[£,\s]", "", text))
    return int(round(amount))


def norm_ws(text: str) -> str:
    return " ".join(text.split())


def crn_value(raw: str) -> str:
    """Companies House numbers are 8 characters; brochures sometimes drop the leading zero."""
    raw = raw.upper()
    prefix = re.match(r"[A-Z]*", raw).group(0)
    return prefix + raw[len(prefix):].zfill(8 - len(prefix))


def tenant_name(raw: str) -> str:
    name = re.split(r"\(?\s*crn\b|\(?\s*t/a\b|\bwith personal", raw, maxsplit=1,
                    flags=re.IGNORECASE)[0]
    name = norm_ws(_MARKER_RE.sub(" ", name)).strip(" ,-(")
    if name.isupper():
        name = " ".join(_title_word(w) for w in name.split())
    return name


def _title_word(word: str) -> str:
    core = word.strip("().,&")
    if core in _ACRONYMS:
        return word
    return word.lower().capitalize() if word[:1].isalpha() else word[:1] + word[1:].lower().capitalize()


def _role(header: str) -> str | None:
    h = re.sub(r"[\s.]", "", header).lower()
    if not h:
        return None
    if "sqft" in h:
        return "sqft"
    if "sqm" in h or "acre" in h or "ha" == h[-2:] or "hectare" in h:
        return None
    if "tenant" in h:
        return "tenant"
    if h.startswith("term"):
        return "term"
    if "review" in h or "reversion" in h:
        return "review"
    if h.startswith("rent"):
        return "rent"
    if any(h.startswith(u) for u in _UNIT_HEADERS):
        return "unit"
    return None


def _cell(page: pymupdf.Page, bbox) -> str:
    if bbox is None:
        return ""
    return page.get_textbox(pymupdf.Rect(bbox)).strip()


def find_table(pdf: pymupdf.Document) -> Table | None:
    for pno, page in enumerate(pdf):
        text = page.get_text()
        if "Tenancy" not in text or "Accommodation" not in text:
            continue
        for tb in page.find_tables().tables:
            if not tb.rows:
                continue
            header = [_cell(page, c) for c in tb.rows[0].cells]
            roles = [_role(h) for h in header]
            if "tenant" not in roles or "rent" not in roles:
                continue
            table = _read_rows(page, pno + 1, tb, roles)
            table.reversion_only = any(
                "reversion" in h.lower() and "review" not in h.lower() for h in header)
            return table
    return None


def _lines(page: pymupdf.Page, bbox) -> list[tuple[float, str]]:
    """(y0, text) of each text line inside a cell, top to bottom."""
    if bbox is None:
        return []
    rect = pymupdf.Rect(bbox)
    grouped: dict[tuple[int, int], list] = {}
    for w in page.get_text("words", clip=rect):
        grouped.setdefault((w[5], w[6]), []).append(w)
    out = [(min(w[1] for w in ws), " ".join(w[4] for w in sorted(ws, key=lambda w: w[0])))
           for ws in grouped.values()]
    return sorted(out)


def _split_by_rent(page: pymupdf.Page, cells_bbox: list, roles: list[str | None]) -> list[list[str]] | None:
    """Split one merged multi-tenant row into one text list per lease, anchored on rent lines.

    Floor-only tables put several leases in one ruled row, so find_tables returns them merged.
    """
    rent_idx = roles.index("rent")
    rent_lines = _lines(page, cells_bbox[rent_idx])
    anchors = [y for i, (y, t) in enumerate(rent_lines)
               if re.match(r"£|peppercorn|nil\b", t, re.IGNORECASE)
               and not (i and re.search(r"\b(?:to|and|plus)\s*$", rent_lines[i - 1][1], re.I))]
    if len(anchors) < 2:
        return None
    bounds = [a - 2 for a in anchors] + [float("inf")]
    per_col = [_lines(page, b) for b in cells_bbox]
    out: list[list[str]] = []
    for i in range(len(anchors)):
        lo, hi = (bounds[i] if i else -float("inf")), bounds[i + 1]
        out.append(["\n".join(t for y, t in col if lo <= y < hi) for col in per_col])
    return out


def _read_rows(page: pymupdf.Page, page_no: int, tb, roles: list[str | None]) -> Table:
    has_unit_col = "unit" in roles
    rows: list[Row] = []
    total: dict[str, str] | None = None
    raw_rows: list[list[str]] = []
    for r in tb.rows[1:]:
        texts = [_cell(page, c) for c in r.cells]
        split = None if re.match(r"total", texts[0] if texts else "", re.I) \
            else _split_by_rent(page, r.cells, roles)
        raw_rows += split or [texts]
    lease_cols = [i for i, r in enumerate(roles) if r in ("tenant", "rent")]

    def starts_lease(texts: list[str]) -> bool:
        return any(texts[i].strip() for i in lease_cols if i < len(texts))

    multi = not has_unit_col and sum(
        1 for t in raw_rows if not re.match(r"total", t[0], re.I) and starts_lease(t)) > 1
    for texts in raw_rows:
        first = texts[0] if texts else ""
        if re.match(r"total", first, re.IGNORECASE):
            total = {role: t for role, t in zip(roles, texts) if role}
            continue
        if not any(texts):
            continue
        cells = {role: t for role, t in zip(roles, texts) if role and role != "unit"}
        unit_text = norm_ws(_MARKER_RE.sub(" ", texts[roles.index("unit")])) if has_unit_col else ""
        if has_unit_col and not unit_text and rows:
            prev = rows[-1]
            for k, v in cells.items():
                if v:
                    prev.cells[k] = (prev.cells.get(k, "") + "\n" + v).strip()
            continue
        if not has_unit_col and rows and not (multi and starts_lease(texts)):
            prev = rows[-1]
            for k, v in cells.items():
                if v:
                    prev.cells[k] = (prev.cells.get(k, "") + "\n" + v).strip()
            continue
        if has_unit_col:
            unit_id = unit_text
        elif multi:
            unit_id = norm_ws(_MARKER_RE.sub(" ", texts[0])) or f"Lease {len(rows) + 1}"
        else:
            unit_id = WHOLE_UNIT
        markers = set()
        for role in ("tenant", "term", "rent", "review"):
            markers |= set(_MARKER_RE.findall(cells.get(role, "")))
        if has_unit_col:
            markers |= set(_MARKER_RE.findall(texts[roles.index("unit")]))
        rows.append(Row(unit_id=unit_id, cells=cells, markers=markers))
    _dedupe_unit_ids(rows)
    return Table(page=page_no, roles=roles, rows=rows, total=total)


def _dedupe_unit_ids(rows: list[Row]) -> None:
    seen: dict[str, int] = {}
    for row in rows:
        n = seen.get(row.unit_id, 0) + 1
        seen[row.unit_id] = n
        if n > 1:
            row.unit_id = f"{row.unit_id} #{n}"


def _label(doc_id: str, unit_id: str | None, field_: str, value, page: int, quote: str) -> Label:
    value = FIELD_TYPES[field_](value) if value is not None else None
    return Label(doc_id=doc_id, unit_id=unit_id, field=field_, value=value, page=page,
                 quote=quote.strip(), label_source="parser", status="agreed")


def _areas(text: str) -> tuple[int | None, str]:
    """Sum of sq ft values in a cell; parenthesised values win, tiny ones are footnote markers."""
    matches = [m for m in _PAREN_NUM_RE.finditer(text) if int(m.group(1).replace(",", "")) >= 30]
    if not matches:
        bare = [m for m in _BARE_NUM_RE.finditer(_MARKER_RE.sub(" ", text))
                if int(m.group(0).replace(",", "")) >= 30]
        if not bare:
            return None, ""
        matches = bare
    total = sum(int(m.group(m.lastindex or 0).replace(",", "")) for m in matches)
    quote = text[matches[0].start():matches[-1].end()]
    return total, quote


def _pick_date(dates: list[date], vdate: date) -> date | None:
    future = [d for d in dates if d >= vdate]
    if future:
        return min(future)
    return max(dates) if dates else None


def _rent(text: str, vdate: date) -> tuple[int | None, str]:
    step = _STEP_RE.search(text)
    if step:
        when = parse_date(step.group(3))
        amount = step.group(2) if when and when <= vdate else step.group(1)
        return money(amount), step.group(0)
    m = re.search(_MONEY, text)
    if m:
        return money(m.group(0)), m.group(0)
    m = re.search(r"peppercorn|\bnil\b", text, re.IGNORECASE)
    if m:
        return 0, m.group(0)
    return None, ""


def _unit_labels(doc_id: str, row: Row, page: int, vdate: date,
                 reversion_only: bool = False) -> list[Label]:
    out: list[Label] = []
    uid = row.unit_id
    c = row.cells

    tenant_raw = c.get("tenant", "")
    vacant = bool(re.search(r"\bvacant\b", tenant_raw, re.IGNORECASE))
    if tenant_raw:
        name = "Vacant" if vacant else tenant_name(tenant_raw)
        quote = re.match(r"\s*vacant(?:\s+possession)?", tenant_raw, re.IGNORECASE)
        if vacant and quote:
            out.append(_label(doc_id, uid, "tenant", name, page, quote.group(0)))
        elif name:
            q = re.split(r"\(?\s*crn\b|\(?\s*t/a\b", tenant_raw, maxsplit=1,
                         flags=re.IGNORECASE)[0]
            out.append(_label(doc_id, uid, "tenant", name, page,
                              _MARKER_RE.sub("", q).strip(" ,(\n")))
        crn = _CRN_RE.search(tenant_raw)
        if crn and not vacant:
            out.append(_label(doc_id, uid, "tenant_crn", crn_value(crn.group(1)), page,
                              crn.group(1)))

    area, quote = _areas(c.get("sqft", ""))
    if area is not None:
        out.append(_label(doc_id, uid, "area_sqft", area, page, quote))

    rent_text = c.get("rent", "")
    rent, quote = _rent(rent_text, vdate)
    if rent is not None:
        out.append(_label(doc_id, uid, "passing_rent", rent, page, quote))
    elif vacant:
        out.append(_label(doc_id, uid, "passing_rent", 0, page, tenant_raw.split("\n")[0]))

    term_text = c.get("term", "")
    term = _TERM_RE.search(term_text)
    if term:
        start = parse_date(term.group(2))
        if start:
            out.append(_label(doc_id, uid, "lease_start", start.isoformat(), page, term.group(0)))
        out.append(_label(doc_id, uid, "lease_term_years", float(term.group(1)), page, term.group(0)))

    review_text = c.get("review", "")
    bracketed = list(re.finditer(rf"\(\s*({_DATE})\s*\)", review_text))
    expiry_quote = ""
    expiry = None
    if bracketed:
        expiry = parse_date(bracketed[-1].group(1))
        expiry_quote = bracketed[-1].group(0)
    elif (until := _UNTIL_RE.search(term_text)):
        expiry = parse_date(until.group(1))
        expiry_quote = until.group(1)
    review_dates_text = re.sub(rf"\(\s*{_DATE}\s*\)", " ", review_text)
    review_matches = list(re.finditer(_DATE, review_dates_text))
    if reversion_only:
        if expiry is None and review_matches:
            expiry = parse_date(review_matches[-1].group(0))
            expiry_quote = review_matches[-1].group(0)
        review_matches = []
    if expiry:
        out.append(_label(doc_id, uid, "lease_expiry", expiry.isoformat(), page, expiry_quote))

    reviews = [(parse_date(m.group(0)), m.group(0)) for m in review_matches]
    reviews = [(d, q) for d, q in reviews if d]
    picked = _pick_date([d for d, _ in reviews], vdate)
    if picked:
        q = next(q for d, q in reviews if d == picked)
        out.append(_label(doc_id, uid, "rent_review_date", picked.isoformat(), page, q))
    return out


def _footnotes(page_text: str) -> dict[str, str]:
    return {m.group(1): m.group(2) for m in _FOOTNOTE_RE.finditer(page_text)}


def _clause_start(text: str, pos: int) -> int:
    head = text[:pos]
    cut = max(head.rfind(". "), head.rfind(".\n"), head.rfind(", "), head.rfind(";"))
    return cut + 2 if cut >= 0 else 0


def _break_from_text(text: str, vdate: date) -> tuple[date, str] | None:
    m = _BREAK_RE.search(text)
    if not m:
        return None
    pre = text[max(0, m.start() - 60):m.start()].lower()
    if "landlord" in pre and "tenant" not in pre and "mutual" not in pre:
        return None
    raw = [m.group(1)] + re.findall(_ANY_DATE, m.group(2) or "")
    dates = [d for d in (parse_date(x) for x in raw) if d]
    picked = _pick_date(dates, vdate)
    if not picked:
        return None
    start = _clause_start(text, m.start())
    end = m.end(1)
    if end < len(text) and text[end] == "." and not (m.group(2) or "").strip():
        end += 1
    return picked, text[start:end].strip()


def _match_unit(note: str, rows: list[Row], number: str) -> list[Row]:
    as_to = re.match(r"\s*As to (?:the )?(.+?),", note, re.IGNORECASE)
    if as_to:
        target = norm_ws(as_to.group(1)).lower()
        hits = [r for r in rows if norm_ws(r.unit_id).lower() == target]
        if not hits:
            hits = [r for r in rows if r.unit_id.lower() in target or target in r.unit_id.lower()]
        if hits:
            return hits
    hits = [r for r in rows if number in r.markers]
    if hits:
        return hits
    return rows if len(rows) == 1 else []


def break_labels(doc_id: str, table: Table, page_text: str, vdate: date) -> list[Label]:
    out: list[Label] = []
    done: set[str] = set()
    for number, note in _footnotes(page_text).items():
        found = _break_from_text(note, vdate)
        if not found:
            continue
        when, quote = found
        for row in _match_unit(note, table.rows, number):
            if row.unit_id in done:
                continue
            done.add(row.unit_id)
            out.append(_label(doc_id, row.unit_id, "break_date", when.isoformat(), table.page, quote))
    for row in table.rows:
        if row.unit_id in done:
            continue
        found = _break_from_text(row.cells.get("term", ""), vdate)
        if found:
            done.add(row.unit_id)
            out.append(_label(doc_id, row.unit_id, "break_date", found[0].isoformat(),
                              table.page, found[1]))
    return out


def total_labels(doc_id: str, table: Table) -> list[Label]:
    out: list[Label] = []
    if not table.total:
        return out
    area, quote = _areas(table.total.get("sqft", ""))
    if area is not None:
        out.append(_label(doc_id, None, "total_area_sqft", area, table.page, quote))
    m = re.search(_MONEY, table.total.get("rent", ""))
    if m:
        out.append(_label(doc_id, None, "total_rent", money(m.group(0)), table.page, m.group(0)))
    return out


def parse(path: str | Path, doc_id: str | None = None) -> list[Label]:
    doc_id = doc_id or doc_id_from_path(path)
    vdate = valuation_date()
    with pymupdf.open(path) as pdf:
        table = find_table(pdf)
    if table is None:
        return []
    labels: list[Label] = []
    for row in table.rows:
        labels += _unit_labels(doc_id, row, table.page, vdate, table.reversion_only)
    text = page_texts(path)[table.page - 1]
    labels += break_labels(doc_id, table, text, vdate)
    labels += total_labels(doc_id, table)
    return labels


def parse_tenancy(pdf_path: str | Path) -> list[Label]:
    return parse(pdf_path, doc_id_from_path(pdf_path))
