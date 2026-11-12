"""Grok extractor: page text in, tenancy schedule with verbatim quotes and pages out.

variant "a" is the main prompt (extractor "grok"); variant "b" uses different wording and field
order (extractor "grok-b") and serves as the second extractor when V7 Go is unavailable.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

from core.models import PROPERTY_FIELDS, UNIT_FIELDS, ExtractionRun, Fact, utcnow
from core.pdf import page_texts
from extract.common import doc_id_for, facts_from_payload, read_cache, write_cache

PROMPT_VERSION = {"a": "grok-a-v1", "b": "grok-b-v1"}
EXTRACTOR = {"a": "grok", "b": "grok-b"}

FIELD_HINTS: dict[str, str] = {
    "tenant": "tenant name as printed",
    "tenant_crn": "Companies House number (CRN), 8 characters, keep leading zeros",
    "area_sqft": "floor area in sq ft (the figure in brackets after sq m), integer; sum floors of the unit",
    "passing_rent": "annual rent in whole GBP payable now (exclusive of VAT); for stepped rent the current amount",
    "lease_start": "lease start date as printed (dd/mm/yyyy)",
    "lease_term_years": "lease term in years, number",
    "lease_expiry": "lease expiry / reversion date as printed, often in brackets (dd/mm/yyyy)",
    "break_date": "first tenant break (option to determine) date; often only in footnotes",
    "rent_review_date": "next rent review date as printed",
    "address": "full property address",
    "property_type": "property type, e.g. Industrial, Retail, Office",
    "total_rent": "total annual rent in whole GBP",
    "total_area_sqft": "total floor area in sq ft, integer",
    "niy_pct": "net initial yield in percent, only if printed in the document",
}

SYSTEM_A = """You extract the tenancy schedule from a UK commercial property sale brochure.
Rules:
- Return one item in "units" per row of the tenancy schedule table (e.g. 'Tenancy & Accommodation'). unit_id is the text of the first column, e.g. "Unit 1".
- For every field return value, quote and page. quote must be copied verbatim from the page text (exact characters, no paraphrase); page is the 1-based number from the "=== PAGE n ===" marker.
- If a value is not stated in the document, return null for value, quote and page. Never guess or compute values that are not printed.
- Tenant breaks are often only in footnotes under the table ("option to determine the lease on ..."). Always scan the footnotes.
- Dates: return exactly as printed (UK dd/mm/yyyy). Money: whole pounds as a number, annual, exclusive of VAT. Area: sq ft from the brackets, not converted from sq m.
- Stepped rent ("£20,000 rising to £25,500 in April 2027"): passing_rent is the amount payable now.
- Return values as strings; numbers without currency symbols or commas."""

SYSTEM_B = """Task: read the brochure text below and fill the JSON schema for every let unit listed in the tenancy table.
Each field is an object {value, quote, page}:
* value - normalised value (plain number for money/area/years, date string exactly as written for dates);
* quote - the shortest exact substring of the page text that supports the value, copied character for character;
* page - the page number shown in the "=== PAGE n ===" header where the quote appears.
Leave value, quote and page null when the brochure does not state the value; inventing or inferring is an error.
Notes: footnotes below the table may contain tenant break options ("option to determine"); the reversion/expiry date is printed in brackets; the sq ft figure is in brackets after sq m; for rent that steps up later, use today's rent. A yield is usually not printed in these brochures."""


def _cell_schema(field: str) -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "description": FIELD_HINTS[field],
        "properties": {
            "value": {"type": ["string", "null"]},
            "quote": {"type": ["string", "null"]},
            "page": {"type": ["integer", "null"]},
        },
        "required": ["value", "quote", "page"],
    }


def json_schema(variant: str = "a") -> dict:
    unit_fields = list(UNIT_FIELDS) if variant == "a" else list(reversed(UNIT_FIELDS))
    prop_fields = list(PROPERTY_FIELDS) if variant == "a" else list(reversed(PROPERTY_FIELDS))
    unit = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"unit_id": {"type": "string"}, **{f: _cell_schema(f) for f in unit_fields}},
        "required": ["unit_id", *unit_fields],
    }
    prop = {
        "type": "object",
        "additionalProperties": False,
        "properties": {f: _cell_schema(f) for f in prop_fields},
        "required": prop_fields,
    }
    if variant == "a":
        props, req = {"units": {"type": "array", "items": unit}, "property": prop}, ["units", "property"]
    else:
        props, req = {"property": prop, "units": {"type": "array", "items": unit}}, ["property", "units"]
    return {"type": "object", "additionalProperties": False, "properties": props, "required": req}


def document_text(pdf_path: str | Path) -> str:
    return "\n\n".join(f"=== PAGE {i} ===\n{t}" for i, t in enumerate(page_texts(pdf_path), start=1))


def _cost(prompt_tokens: int | None, completion_tokens: int | None) -> float | None:
    p_in, p_out = os.getenv("GROK_PRICE_IN_PER_M"), os.getenv("GROK_PRICE_OUT_PER_M")
    if not p_in or not p_out or prompt_tokens is None or completion_tokens is None:
        return None
    return round(prompt_tokens * float(p_in) / 1e6 + completion_tokens * float(p_out) / 1e6, 6)


def call_grok(text: str, variant: str, model: str) -> dict:
    """One structured-output call. Returns {"payload": dict, "usage": dict, "model": str}."""
    import json

    from openai import OpenAI

    client = OpenAI(api_key=os.environ["XAI_API_KEY"], base_url="https://api.x.ai/v1", timeout=300)
    resp = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SYSTEM_A if variant == "a" else SYSTEM_B},
            {"role": "user", "content": text},
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "tenancy_schedule", "schema": json_schema(variant), "strict": True},
        },
        reasoning_effort="low",
    )
    usage = resp.usage.model_dump() if resp.usage else {}
    return {
        "payload": json.loads(resp.choices[0].message.content or "{}"),
        "usage": usage,
        "model": resp.model or model,
    }


def parse(raw: dict, doc_id: str, variant: str, run_id: str) -> list[Fact]:
    model = raw.get("model") or os.getenv("GROK_MODEL", "grok")
    version = f"{model}@{PROMPT_VERSION[variant]}"
    return facts_from_payload(raw.get("payload") or {}, doc_id, extractor=EXTRACTOR[variant],
                              author=version, model_version=version, run_id=run_id)


def extract_grok(pdf_path: str | Path, variant: str = "a") -> tuple[list[Fact], ExtractionRun]:
    if variant not in EXTRACTOR:
        raise ValueError(f"unknown variant {variant!r}")
    load_dotenv(".env")
    extractor = EXTRACTOR[variant]
    doc_id = doc_id_for(pdf_path)
    model = os.getenv("GROK_MODEL", "grok-4.6")
    started = utcnow()
    raw = read_cache(extractor, doc_id)
    if raw is None:
        raw = call_grok(document_text(pdf_path), variant, model)
        raw.update({"doc_id": doc_id, "prompt_version": PROMPT_VERSION[variant], "called_at": started.isoformat()})
        write_cache(extractor, doc_id, raw)
    usage = raw.get("usage") or {}
    run = ExtractionRun(
        extractor=extractor,
        model=raw.get("model") or model,
        prompt_version=PROMPT_VERSION[variant],
        started_at=started,
        finished_at=utcnow(),
        tokens=usage.get("total_tokens"),
        cost=_cost(usage.get("prompt_tokens"), usage.get("completion_tokens")),
        doc_ids=[doc_id],
    )
    return parse(raw, doc_id, variant, run.id), run
