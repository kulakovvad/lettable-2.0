"""Shared helpers: doc ids, raw cache, and conversion of the extraction JSON into Fact rows.

Both extractors return the same shape (see scripts/v7_setup.json_schema):
{"units": [{"unit_id": str, <field>: {"value", "quote", "page", ...}}], <property field>: {...}}
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from core.models import FIELD_TYPES, FIELD_UNITS, PROPERTY_FIELDS, UNIT_FIELDS, Fact, Value

RAW_DIR = Path("data/raw")
DOC_ID_RE = re.compile(r"^(acuitus-[0-9a-f]+)")


def doc_id_for(pdf_path: str | Path) -> str:
    stem = Path(pdf_path).stem
    m = DOC_ID_RE.match(stem)
    return m.group(1) if m else stem.split("_")[0]


def cache_path(extractor: str, doc_id: str) -> Path:
    return RAW_DIR / extractor / f"{doc_id}.json"


def read_cache(extractor: str, doc_id: str) -> dict | None:
    p = cache_path(extractor, doc_id)
    if p.exists():
        return json.loads(p.read_text())
    return None


def write_cache(extractor: str, doc_id: str, data: dict) -> None:
    p = cache_path(extractor, doc_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data, indent=1, ensure_ascii=False, default=str))


_NUM_RE = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def coerce(field: str, raw: Any) -> Value:
    """Cast a raw model value to FIELD_TYPES[field]; None when it cannot be read.

    Dates stay as printed (normalisation is verify/dates). CRN keeps leading zeros.
    """
    if raw is None:
        return None
    if isinstance(raw, str):
        raw = raw.strip()
        if not raw or raw.lower() in {"null", "none", "n/a", "nil", "-"}:
            return None
    typ = FIELD_TYPES[field]
    if field == "tenant_crn":
        s = re.sub(r"\s+", "", str(raw))
        if s.isdigit() and len(s) < 8:
            s = s.zfill(8)
        return s
    if typ is str:
        return str(raw)
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return int(round(raw)) if typ is int else float(raw)
    m = _NUM_RE.search(str(raw))
    if not m:
        return None
    num = float(m.group(0).replace(",", ""))
    return int(round(num)) if typ is int else num


def _page(raw: Any) -> int | None:
    try:
        p = int(raw)
    except (TypeError, ValueError):
        return None
    return p if p >= 1 else None


def _fact(doc_id: str, unit_id: str | None, field: str, cell: Any, meta: dict) -> Fact | None:
    if not isinstance(cell, dict):
        cell = {"value": cell}
    value = coerce(field, cell.get("value"))
    if value is None:
        return None
    quote = cell.get("quote")
    quote = quote.strip() if isinstance(quote, str) and quote.strip() else None
    return Fact(
        doc_id=doc_id,
        unit_id=unit_id,
        field=field,
        value=value,
        unit=FIELD_UNITS[field],
        page=_page(cell.get("page")),
        quote=quote,
        bbox=cell.get("bbox"),
        confidence=cell.get("confidence"),
        **meta,
    )


def facts_from_payload(payload: dict, doc_id: str, *, extractor: str, author: str,
                       model_version: str | None, run_id: str | None) -> list[Fact]:
    """Fact per non-null field. Missing values are omitted, not stored as null facts."""
    meta = {"extractor": extractor, "author": author, "model_version": model_version, "run_id": run_id}
    out: list[Fact] = []
    seen: set[str] = set()
    for i, unit in enumerate(payload.get("units") or []):
        if not isinstance(unit, dict):
            continue
        uid = str(unit.get("unit_id") or f"Unit {i + 1}").strip()
        base, n = uid, 2
        while uid in seen:
            uid, n = f"{base} ({n})", n + 1
        seen.add(uid)
        for field in UNIT_FIELDS:
            f = _fact(doc_id, uid, field, unit.get(field), meta)
            if f:
                out.append(f)
    prop = payload.get("property") if isinstance(payload.get("property"), dict) else payload
    for field in PROPERTY_FIELDS:
        f = _fact(doc_id, None, field, prop.get(field), meta)
        if f:
            out.append(f)
    return out
