"""MSCI RCA exit yield: median UK cap rate for one MAINTYPE over 24 months, as a Snapshot.

RCA rows are licensed: only deal ids and the hash go to the database, the rows themselves stay in
data/raw/rca/<maintype>.json (git-ignored). A rerun reads that cache and never queries live.
Login is MSCI SSO (externalbrowser); there is no password path.
"""

from __future__ import annotations

import hashlib
import json
import os
from datetime import date, datetime
from pathlib import Path
from statistics import median
from typing import Any

from dotenv import load_dotenv

from core.models import Snapshot, new_id, utcnow, valuation_date

load_dotenv()

RAW_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "rca"

QUERY = """SELECT DEAL_ID, PROPERTY_ID, CAPRATE, STATUS_DT
FROM MSCI_ARES.REAL_ESTATE.RCA_ARES_TRANSACTION
WHERE ACTIVE_FG = TRUE AND STATUS_TX = 'closed' AND COUNTRY_TX = 'United Kingdom'
  AND MAINTYPE = %(maintype)s AND CAPRATE IS NOT NULL
  AND STATUS_DT >= DATEADD(month, -24, %(valuation_date)s::date)
ORDER BY DEAL_ID"""

MAINTYPES: dict[str | None, str] = {
    "industrial": "Industrial",
    "retail": "Retail",
    "office": "Office",
    "leisure": "Hotel",
}

Row = tuple[str, str, float]  # DEAL_ID, PROPERTY_ID, CAPRATE (fraction)


def maintype(sector: str | None) -> str:
    return MAINTYPES.get(sector, "Other")


def render_query(mt: str, as_of: date) -> str:
    return QUERY.replace("%(maintype)s", f"'{mt}'").replace("%(valuation_date)s", f"'{as_of.isoformat()}'")


def result_hash(rows: list[Row]) -> str:
    payload = json.dumps(sorted((str(d), str(p), float(c)) for d, p, c in rows))
    return hashlib.sha256(payload.encode()).hexdigest()


def median_pct(caprates: list[float]) -> float:
    """Median of CAPRATE fractions, in percent (0.064 -> 6.4)."""
    if not caprates:
        raise ValueError("no RCA rows")
    return round(float(median(caprates)) * 100, 2)


def snapshot_from_cache(cache: dict[str, Any]) -> Snapshot:
    rows: list[Row] = [tuple(r) for r in cache["rows"]]  # type: ignore[misc]
    return Snapshot(
        id=cache["snapshot_id"],
        provider="rca",
        query_sql=cache["query_sql"],
        executed_at=datetime.fromisoformat(cache["executed_at"]),
        row_count=len(rows),
        result_sha256=result_hash(rows),
        row_ids=[str(r[0]) for r in rows],
    )


def _account() -> str:
    acc = os.environ["SNOWFLAKE_ACCOUNT"].strip().removeprefix("https://")
    return acc.split(".snowflakecomputing.com")[0].rstrip("/")


def _fetch(mt: str, as_of: date) -> list[Row]:
    import snowflake.connector

    conn = snowflake.connector.connect(
        account=_account(),
        user=os.environ["SNOWFLAKE_USER"],
        authenticator="externalbrowser",
        client_store_temporary_credential=True,
        role=os.getenv("SNOWFLAKE_ROLE") or "ANALYST",
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE") or "ARES_ANALYST_WH",
        database=os.getenv("SNOWFLAKE_DATABASE") or "MSCI_ARES",
        schema=os.getenv("SNOWFLAKE_SCHEMA") or "REAL_ESTATE",
    )
    try:
        cur = conn.cursor()
        cur.execute(QUERY, {"maintype": mt, "valuation_date": as_of.isoformat()})
        return [(str(d), str(p), float(c)) for d, p, c, _ in cur.fetchall()]
    finally:
        conn.close()


def load(mt: str, as_of: date | None = None, refresh: bool = False) -> dict[str, Any]:
    """Cached query result for one MAINTYPE; queries Snowflake only when there is no cache."""
    path = RAW_DIR / f"{mt}.json"
    if path.exists() and not refresh:
        return json.loads(path.read_text())
    as_of = as_of or valuation_date()
    executed_at = utcnow()
    rows = _fetch(mt, as_of)
    cache = {"maintype": mt, "valuation_date": as_of.isoformat(), "query_sql": render_query(mt, as_of),
             "executed_at": executed_at.isoformat(), "snapshot_id": new_id(), "rows": rows}
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache))
    return cache


def rca_exit_yield(sector: str | None, refresh: bool = False) -> tuple[float, Snapshot]:
    """Median CAPRATE (pct) and the Snapshot that reproduces it; the caller persists the Snapshot."""
    cache = load(maintype(sector), refresh=refresh)
    return median_pct([float(r[2]) for r in cache["rows"]]), snapshot_from_cache(cache)
