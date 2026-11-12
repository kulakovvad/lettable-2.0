"""Frozen data contract shared by every module. Changes go through the integrator."""

from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from typing import Literal

from pydantic import BaseModel, Field

UNIT_FIELDS: tuple[str, ...] = (
    "tenant",
    "tenant_crn",
    "area_sqft",
    "passing_rent",
    "lease_start",
    "lease_term_years",
    "lease_expiry",
    "break_date",
    "rent_review_date",
)

PROPERTY_FIELDS: tuple[str, ...] = (
    "address",
    "property_type",
    "total_rent",
    "total_area_sqft",
    "niy_pct",
)

ALL_FIELDS: tuple[str, ...] = UNIT_FIELDS + PROPERTY_FIELDS

# Python type of `value` per field. Dates are ISO strings, money and area are integer pounds / sq ft,
# tenant_crn is an 8-character string (keep leading zeros).
FIELD_TYPES: dict[str, type] = {
    "tenant": str,
    "tenant_crn": str,
    "area_sqft": int,
    "passing_rent": int,
    "lease_start": str,
    "lease_term_years": float,
    "lease_expiry": str,
    "break_date": str,
    "rent_review_date": str,
    "address": str,
    "property_type": str,
    "total_rent": int,
    "total_area_sqft": int,
    "niy_pct": float,
}

FIELD_UNITS: dict[str, str | None] = {
    "tenant": None,
    "tenant_crn": None,
    "area_sqft": "sqft",
    "passing_rent": "GBP/yr",
    "lease_start": "date",
    "lease_term_years": "years",
    "lease_expiry": "date",
    "break_date": "date",
    "rent_review_date": "date",
    "address": None,
    "property_type": None,
    "total_rent": "GBP/yr",
    "total_area_sqft": "sqft",
    "niy_pct": "pct",
}

DATE_FIELDS: frozenset[str] = frozenset(
    {"lease_start", "lease_expiry", "break_date", "rent_review_date"}
)

Extractor = Literal["v7go", "grok", "grok-b", "manual", "grok-bot", "rca", "fallback"]
ReviewStatus = Literal["auto", "review"]
ReviewReason = Literal[
    "extractors_disagree",
    "ungrounded",
    "date_inconsistent",
    "number_not_in_quote",
    "missing_in_one",
]
DateCheck = Literal["ok", "inconsistent", "not_applicable"]
LabelSource = Literal["parser", "arbiter"]
LabelStatus = Literal["agreed", "disputed"]
Verdict = Literal["caught", "missed", "wrong", "invented", "ungrounded"]

Value = str | int | float | None


def new_id() -> str:
    return uuid.uuid4().hex


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def valuation_date() -> date:
    return date.fromisoformat(os.getenv("VALUATION_DATE", "2026-11-12"))


class Document(BaseModel):
    id: str  # e.g. "acuitus-90f42467"
    filename: str
    sha256: str
    source: str = "acuitus"
    page_count: int
    received_at: datetime = Field(default_factory=utcnow)


class Fact(BaseModel):
    id: str = Field(default_factory=new_id)
    doc_id: str
    unit_id: str | None = None  # None for PROPERTY_FIELDS
    field: str
    value: Value = None
    unit: str | None = None
    page: int | None = None  # 1-based
    bbox: list[float] | None = None  # [x0, y0, x1, y1] in PDF points
    quote: str | None = None  # verbatim text from the page
    grounded: bool | None = None  # None = not checked yet
    date_check: DateCheck | None = None
    confidence: float | None = None
    review_status: ReviewStatus | None = None
    review_reason: ReviewReason | None = None
    run_id: str | None = None
    extractor: str  # see Extractor
    model_version: str | None = None
    author: str  # model@version | person name | grok-bot:<name> | rca@<snapshot_id>
    created_at: datetime = Field(default_factory=utcnow)
    supersedes_id: str | None = None
    reason: str | None = None  # required when supersedes_id is set by a person
    snapshot_id: str | None = None


class Label(BaseModel):
    id: str = Field(default_factory=new_id)
    doc_id: str
    unit_id: str | None = None
    field: str
    value: Value = None
    page: int | None = None
    quote: str | None = None
    label_source: LabelSource = "parser"
    status: LabelStatus = "agreed"


class ProformaVersion(BaseModel):
    id: str = Field(default_factory=new_id)
    doc_id: str
    version_no: int = 1
    trigger_fact_id: str | None = None
    author: str
    reason: str | None = None
    created_at: datetime = Field(default_factory=utcnow)


class Cell(BaseModel):
    id: str = Field(default_factory=new_id)
    version_id: str
    name: str  # e.g. "passing_rent[Unit 1]", "total_rent", "wault_expiry", "value"
    formula: str  # human-readable, e.g. "sum(passing_rent[*])"
    input_fact_ids: list[str] = Field(default_factory=list)
    input_cell_ids: list[str] = Field(default_factory=list)
    value: Value = None
    unit: str | None = None
    author: str | None = None  # set for assumption cells (valuation_date, exit_yield)
    reason: str | None = None
    computed_at: datetime = Field(default_factory=utcnow)


class Snapshot(BaseModel):
    id: str = Field(default_factory=new_id)
    provider: str  # "rca" | "fallback" | "web"
    query_sql: str
    executed_at: datetime = Field(default_factory=utcnow)
    row_count: int
    result_sha256: str
    row_ids: list[str] = Field(default_factory=list)


class ExtractionRun(BaseModel):
    id: str = Field(default_factory=new_id)
    extractor: str
    model: str
    prompt_version: str
    started_at: datetime = Field(default_factory=utcnow)
    finished_at: datetime | None = None
    tokens: int | None = None
    cost: float | None = None  # USD
    doc_ids: list[str] = Field(default_factory=list)


class EvalRow(BaseModel):
    id: str = Field(default_factory=new_id)
    eval_run_id: str  # groups rows of one score() call
    extractor: str
    run_at: datetime = Field(default_factory=utcnow)
    doc_id: str
    unit_id: str | None = None
    field: str
    expected: Value = None
    got: Value = None
    verdict: Verdict
    fact_id: str | None = None
    label_id: str | None = None
    review_status: ReviewStatus | None = None
