"""Analyst benchmark yields quoted from public listed-REIT reports, used when RCA is unavailable.

These are portfolio benchmarks, not comparable transactions: the reason string says so and the UI
must show it. Quotes and pages were checked against the PDFs with pymupdf
(tests/test_market_fallback.py re-checks them when Source/ is present).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from core.models import Fact, Snapshot

from market.types import sector

AUTHOR = "analyst-assumption"
SOURCE_DIR = Path(__file__).resolve().parent.parent / "Source" / "03 - Deal Documents & Brochures"

BENCHMARKS: dict[str, dict[str, Any]] = {
    "segro_hy26": {
        "value": 5.6,
        "path": "SEGRO Investors/press-release-full-version-final.pdf",
        "sha256": "485dca20de026388b38483f5b4c96743c8676a92acc76d056ba488eadf16abaf",
        "page": 4,
        "quote": "Net true equivalent yield (%) 5.6",
        "measure": "SEGRO portfolio net true equivalent yield at 30 June 2026 (UK and European "
                   "industrial and logistics)",
    },
    "british_land_hy27": {
        "value": 6.1,
        "path": "British Land Investors/261015-hy27-trading-update-vf.pdf",
        "sha256": "6684bb6fd642d4e2420073b9833e799ca7c3ad2d1b280eb132764b1ca5f0c87e",
        "page": 2,
        "quote": "NEY +3 bps at 6.1%.",
        "measure": "British Land portfolio net equivalent yield, HY27 trading update (campuses, "
                   "retail parks and London urban logistics)",
    },
}

SECTOR_BENCHMARK: dict[str | None, str] = {
    "industrial": "segro_hy26",
    "retail": "british_land_hy27",
    "office": "british_land_hy27",
    "leisure": "british_land_hy27",
    "mixed": "british_land_hy27",
    None: "british_land_hy27",
}


def benchmark_yield(property_type: str | None) -> dict[str, Any]:
    """Benchmark exit yield (pct) with its public source, page and verbatim quote."""
    sec = sector(property_type)
    key = SECTOR_BENCHMARK[sec]
    b = BENCHMARKS[key]
    mapped = (f"property type {property_type!r} mapped to sector {sec!r}" if sec
              else f"property type {property_type!r} not mapped to a sector; default benchmark used")
    reason = (f"Analyst benchmark, not MSCI RCA: {b['measure']}. {mapped}. A listed-REIT portfolio "
              f"yield is not a comparable transaction; replace with RCA or a manual yield when "
              f"available.")
    return {
        "key": key,
        "value": b["value"],
        "sector": sec,
        "source": Path(b["path"]).name,
        "sha256": b["sha256"],
        "page": b["page"],
        "quote": b["quote"],
        "author": AUTHOR,
        "reason": reason,
    }


def benchmark_snapshot(bench: dict[str, Any]) -> Snapshot:
    """Reproducible record of which benchmark was used: file hash, page, quote and value."""
    payload = json.dumps({k: bench[k] for k in ("sha256", "page", "quote", "value")}, sort_keys=True)
    return Snapshot(
        id=f"fallback-{bench['key']}",
        provider="fallback",
        query_sql=(f"benchmark_yield(sector={bench['sector']!r}) -> {bench['source']} "
                   f"sha256={bench['sha256']} page={bench['page']}"),
        row_count=1,
        result_sha256=hashlib.sha256(payload.encode()).hexdigest(),
        row_ids=[bench["key"]],
    )


def benchmark_fact(doc_id: str, bench: dict[str, Any], snapshot_id: str | None = None) -> Fact:
    """Assumption fact attached to the property document; the quote and page refer to bench['source']."""
    return Fact(
        doc_id=doc_id,
        field="exit_yield",
        value=bench["value"],
        unit="pct",
        page=bench["page"],
        quote=bench["quote"],
        extractor="fallback",
        model_version=f"benchmark:{bench['key']}",
        author=AUTHOR,
        reason=f"{bench['reason']} Source: {bench['source']} (sha256 {bench['sha256'][:12]}), "
               f"page {bench['page']}.",
        snapshot_id=snapshot_id,
    )


def exit_yield(doc_id: str, property_type: str | None = None) -> Fact:
    """CONTRACTS.md form: benchmark fact for a document, not persisted."""
    return benchmark_fact(doc_id, benchmark_yield(property_type))


def source_pdf(key: str) -> Path:
    return SOURCE_DIR / BENCHMARKS[key]["path"]
