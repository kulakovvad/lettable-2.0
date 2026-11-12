"""Exit yield for the proforma: MSCI RCA snapshot first, analyst benchmark with a quote otherwise."""

from __future__ import annotations

import logging
from typing import Any

from core.models import Fact, Snapshot

from market import fallback, rca
from market.types import sector

log = logging.getLogger(__name__)


def _persist(fact: Fact, snapshot: Snapshot) -> tuple[str | None, str | None]:
    """Write snapshot and fact once; without a database the value still carries its source in `reason`."""
    try:
        from core import store

        for f in store.current_facts(doc_id=fact.doc_id, extractor=fact.extractor):
            if f.field == "exit_yield" and f.value == fact.value and f.snapshot_id == snapshot.id:
                return f.snapshot_id, f.id
        if store.get_snapshot(snapshot.id) is None:
            store.insert_snapshot(snapshot)
        store.insert_facts([fact])
        return snapshot.id, fact.id
    except Exception as exc:
        log.warning("exit yield not persisted: %s", exc)
        return None, None


def rca_fact(doc_id: str, value: float, snap: Snapshot, sec: str | None) -> Fact:
    mt = rca.maintype(sec)
    return Fact(
        doc_id=doc_id, field="exit_yield", value=value, unit="pct", extractor="rca",
        model_version=f"rca:{mt}", author=f"rca@{snap.id}", snapshot_id=snap.id,
        reason=(f"Median CAPRATE of {snap.row_count} closed UK {mt} deals in MSCI RCA, 24 months to "
                f"the valuation date, ACTIVE_FG = TRUE (sector {sec or 'unmapped'!s}, result sha256 "
                f"{snap.result_sha256[:12]})."),
    )


def get_exit_yield(doc_id: str, property_type: str | None = None) -> dict[str, Any]:
    """Dict for core.proforma.build(exit_yield=...): value (pct), snapshot_id, source_fact_id,
    author, reason, plus provider/page/quote/source/sample_size for the UI."""
    sec = sector(property_type)
    try:
        value, snap = rca.rca_exit_yield(sec)
        fact = rca_fact(doc_id, value, snap, sec)
        snapshot_id, fact_id = _persist(fact, snap)
        return {"value": value, "snapshot_id": snapshot_id or snap.id, "source_fact_id": fact_id,
                "author": f"rca@{snap.id}", "reason": fact.reason, "provider": "rca",
                "page": None, "quote": None, "source": "MSCI RCA", "sample_size": snap.row_count}
    except Exception as exc:
        log.warning("RCA unavailable, using benchmark: %s", exc)
    bench = fallback.benchmark_yield(property_type)
    snap = fallback.benchmark_snapshot(bench)
    fact = fallback.benchmark_fact(doc_id, bench, snapshot_id=snap.id)
    snapshot_id, fact_id = _persist(fact, snap)
    return {"value": bench["value"], "snapshot_id": snapshot_id, "source_fact_id": fact_id,
            "author": fallback.AUTHOR, "reason": fact.reason, "provider": "fallback",
            "page": bench["page"], "quote": bench["quote"], "source": bench["source"],
            "sample_size": 1}
