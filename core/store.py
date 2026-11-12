"""The only module that talks to Postgres. Connection string comes from DATABASE_URL."""

from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

import psycopg
from dotenv import load_dotenv
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from core.models import (
    Cell,
    Document,
    EvalRow,
    ExtractionRun,
    Fact,
    Label,
    ProformaVersion,
    Snapshot,
    utcnow,
)

load_dotenv()

SCHEMA_PATH = Path(__file__).resolve().parent.parent / "db" / "schema.sql"
JSON_COLUMNS = {"value", "expected", "got"}


def database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if not url:
        raise RuntimeError("DATABASE_URL is not set (see .env.example)")
    return url


@contextmanager
def connect() -> Iterator[psycopg.Connection]:
    with psycopg.connect(database_url(), row_factory=dict_row) as conn:
        yield conn


def apply_schema() -> None:
    with connect() as conn:
        conn.execute(SCHEMA_PATH.read_text())


def reset_schema() -> None:
    """Drop and recreate everything. Dev only: used by scripts/load_fixtures.py --reset."""
    with connect() as conn:
        conn.execute("drop schema public cascade; create schema public;")
        conn.execute(SCHEMA_PATH.read_text())


def _insert(table: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    cols = list(rows[0].keys())
    sql = f"insert into {table} ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))})"
    params = [
        tuple(Jsonb(r[c]) if c in JSON_COLUMNS else r[c] for c in cols) for r in rows
    ]
    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(sql, params)


def _select(sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    with connect() as conn:
        return conn.execute(sql, params).fetchall()


def _where(filters: dict[str, Any]) -> tuple[str, tuple[Any, ...]]:
    used = {k: v for k, v in filters.items() if v is not None}
    if not used:
        return "", ()
    return " where " + " and ".join(f"{k} = %s" for k in used), tuple(used.values())


# documents

def insert_document(doc: Document) -> str:
    with connect() as conn:
        conn.execute(
            "insert into documents (id, filename, sha256, source, page_count, received_at) "
            "values (%s, %s, %s, %s, %s, %s) on conflict (id) do nothing",
            (doc.id, doc.filename, doc.sha256, doc.source, doc.page_count, doc.received_at),
        )
    return doc.id


def documents() -> list[Document]:
    return [Document(**r) for r in _select("select * from documents order by id")]


def get_document(doc_id: str) -> Document | None:
    rows = _select("select * from documents where id = %s", (doc_id,))
    return Document(**rows[0]) if rows else None


# extraction runs

def start_run(run: ExtractionRun) -> str:
    _insert("extraction_runs", [run.model_dump()])
    return run.id


def insert_runs(runs: list[ExtractionRun]) -> None:
    """Idempotent: runs already in the table are left as they are."""
    with connect() as conn:
        with conn.cursor() as cur:
            cur.executemany(
                "insert into extraction_runs (id, extractor, model, prompt_version, started_at, "
                "finished_at, tokens, cost, doc_ids) values (%s, %s, %s, %s, %s, %s, %s, %s, %s) "
                "on conflict (id) do nothing",
                [(r.id, r.extractor, r.model, r.prompt_version, r.started_at, r.finished_at,
                  r.tokens, r.cost, r.doc_ids) for r in runs],
            )


def finish_run(run_id: str, tokens: int | None = None, cost: float | None = None,
               finished_at: datetime | None = None) -> None:
    with connect() as conn:
        conn.execute(
            "update extraction_runs set finished_at = %s, tokens = %s, cost = %s where id = %s",
            (finished_at or utcnow(), tokens, cost, run_id),
        )


def extraction_runs(extractor: str | None = None) -> list[ExtractionRun]:
    where, params = _where({"extractor": extractor})
    rows = _select(f"select * from extraction_runs{where} order by started_at", params)
    return [ExtractionRun(**r) for r in rows]


# facts

def insert_facts(facts: list[Fact]) -> list[str]:
    for f in facts:
        if f.supersedes_id and not (f.reason or "").strip():
            raise ValueError(f"fact {f.id} supersedes {f.supersedes_id} without a reason")
    _insert("facts", [f.model_dump() for f in facts])
    return [f.id for f in facts]


def current_facts(doc_id: str | None = None, extractor: str | None = None) -> list[Fact]:
    where, params = _where({"doc_id": doc_id, "extractor": extractor})
    rows = _select(f"select * from current_facts{where} order by doc_id, unit_id, field", params)
    return [Fact(**r) for r in rows]


def all_facts(doc_id: str | None = None, extractor: str | None = None) -> list[Fact]:
    """Every fact row including superseded ones; evaluation scores extractor output as written."""
    where, params = _where({"doc_id": doc_id, "extractor": extractor})
    rows = _select(f"select * from facts{where} order by doc_id, unit_id, field, created_at", params)
    return [Fact(**r) for r in rows]


def get_fact(fact_id: str) -> Fact | None:
    rows = _select("select * from facts where id = %s", (fact_id,))
    return Fact(**rows[0]) if rows else None


def fact_history(fact_id: str) -> list[Fact]:
    """Whole supersedes chain containing fact_id, oldest first."""
    rows = _select(
        """
        with recursive back as (
            select * from facts where id = %s
            union all
            select f.* from facts f join back b on f.id = b.supersedes_id
        ), fwd as (
            select * from facts where id = %s
            union all
            select f.* from facts f join fwd w on f.supersedes_id = w.id
        )
        select * from back union select * from fwd order by created_at
        """,
        (fact_id, fact_id),
    )
    return [Fact(**r) for r in rows]


# labels

def insert_labels(labels_: list[Label]) -> list[str]:
    _insert("labels", [lb.model_dump() for lb in labels_])
    return [lb.id for lb in labels_]


def labels(doc_id: str | None = None, status: str | None = None) -> list[Label]:
    where, params = _where({"doc_id": doc_id, "status": status})
    rows = _select(f"select * from labels{where} order by doc_id, unit_id, field", params)
    return [Label(**{k: v for k, v in r.items() if k != "created_at"}) for r in rows]


# proforma

def new_version(version: ProformaVersion) -> str:
    _insert("proforma_versions", [version.model_dump()])
    return version.id


def versions(doc_id: str) -> list[ProformaVersion]:
    rows = _select(
        "select * from proforma_versions where doc_id = %s order by version_no", (doc_id,)
    )
    return [ProformaVersion(**r) for r in rows]


def get_version(version_id: str) -> ProformaVersion | None:
    rows = _select("select * from proforma_versions where id = %s", (version_id,))
    return ProformaVersion(**rows[0]) if rows else None


def insert_cells(cells_: list[Cell]) -> list[str]:
    _insert("cells", [c.model_dump() for c in cells_])
    return [c.id for c in cells_]


def cells(version_id: str) -> list[Cell]:
    rows = _select("select * from cells where version_id = %s order by name", (version_id,))
    return [Cell(**r) for r in rows]


def get_cell(cell_id: str) -> Cell | None:
    rows = _select("select * from cells where id = %s", (cell_id,))
    return Cell(**rows[0]) if rows else None


# market snapshots

def insert_snapshot(snapshot: Snapshot) -> str:
    _insert("snapshots", [snapshot.model_dump()])
    return snapshot.id


def get_snapshot(snapshot_id: str) -> Snapshot | None:
    rows = _select("select * from snapshots where id = %s", (snapshot_id,))
    return Snapshot(**rows[0]) if rows else None


# evaluation

def insert_eval(rows: list[EvalRow]) -> list[str]:
    _insert("eval_runs", [r.model_dump() for r in rows])
    return [r.id for r in rows]


def eval_rows(eval_run_id: str | None = None, extractor: str | None = None) -> list[EvalRow]:
    where, params = _where({"eval_run_id": eval_run_id, "extractor": extractor})
    rows = _select(f"select * from eval_runs{where} order by doc_id, unit_id, field", params)
    return [EvalRow(**r) for r in rows]
