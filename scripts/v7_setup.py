"""Create the TraceLease project in V7 Go and print its id. Usage: python -m scripts.v7_setup

Fields mirror core.models.UNIT_FIELDS / PROPERTY_FIELDS; each field returns value, verbatim quote and page.
"""

from __future__ import annotations

import os

import requests
from dotenv import load_dotenv

from core.models import PROPERTY_FIELDS, UNIT_FIELDS

BASE = "https://go.v7labs.com/api"
PROJECT_NAME = "TraceLease - tenancy schedule"

PROMPT = (
    "UK commercial property sale brochure. Extract the tenancy schedule (table 'Tenancy & Accommodation'): "
    "one item in 'units' per lettable unit row. For every field return the value, the verbatim quote copied "
    "exactly from the document, and the 1-based page number. If a value is not in the document, return null "
    "for value, quote and page; never guess. Dates: copy as printed (UK dd/mm/yyyy). Rent: annual GBP exclusive "
    "(p.a.x.); for stepped rent use the amount payable now, not the future step. Area: the sq ft figure in "
    "brackets, summed over floors of the unit. tenant_crn: the Companies House number (CRN), keep leading zeros. "
    "lease_expiry: the reversion date printed in brackets. break_date: tenant option to determine the lease; "
    "these are often only in the footnotes ('As to Unit N ... option to determine the lease on ...'), scan them. "
    "rent_review_date: next rent review date. Property-level fields: full address, property type "
    "(e.g. Industrial, Retail, Office), total rent, total floor area in sq ft, net initial yield in percent."
)


def _field() -> dict:
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "value": {"type": ["string", "null"]},
            "quote": {"type": ["string", "null"]},
            "page": {"type": ["integer", "null"]},
        },
        "required": ["value", "quote", "page"],
    }


def json_schema() -> dict:
    unit = {
        "type": "object",
        "additionalProperties": False,
        "properties": {"unit_id": {"type": "string"}, **{f: _field() for f in UNIT_FIELDS}},
        "required": ["unit_id", *UNIT_FIELDS],
    }
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "units": {"type": "array", "items": unit},
            **{f: _field() for f in PROPERTY_FIELDS},
        },
        "required": ["units", *PROPERTY_FIELDS],
    }


def main() -> None:
    load_dotenv(".env")
    ws = os.environ["V7_WORKSPACE_ID"]
    s = requests.Session()
    s.headers.update({"X-API-KEY": os.environ["V7_API_KEY"], "Accept": "application/json"})

    existing = s.get(f"{BASE}/workspaces/{ws}/projects", timeout=30).json().get("data", [])
    project = next((p for p in existing if p.get("name") == PROJECT_NAME), None)
    if project is None:
        r = s.post(f"{BASE}/workspaces/{ws}/projects",
                   json={"name": PROJECT_NAME, "description": "TraceLease extraction with citations"},
                   timeout=30)
        r.raise_for_status()
        project = r.json()
    pid = project["id"]
    props = s.get(f"{BASE}/workspaces/{ws}/projects/{pid}", timeout=30).json().get("properties", [])
    by_name = {p["name"]: p for p in props}

    if "Brochure" not in by_name:
        r = s.post(f"{BASE}/workspaces/{ws}/projects/{pid}/properties",
                   json={"name": "Brochure", "type": "file", "tool": "manual", "description": "Sale brochure PDF"},
                   timeout=30)
        r.raise_for_status()
        by_name["Brochure"] = r.json()

    if "Tenancy schedule" not in by_name:
        r = s.post(f"{BASE}/workspaces/{ws}/projects/{pid}/properties",
                   json={
                       "name": "Tenancy schedule",
                       "type": "json",
                       "tool": "auto_llm",
                       "description": PROMPT,
                       "is_grounded": True,
                       "inputs": [{"property_id": by_name["Brochure"]["id"], "target": "value"}],
                       "config": {"json_schema": json_schema()},
                   },
                   timeout=30)
        if not r.ok:
            raise SystemExit(f"add property failed: {r.status_code} {r.text[:500]}")
        by_name["Tenancy schedule"] = r.json()

    print(f"V7_PROJECT_ID={pid}")
    for name in ("Brochure", "Tenancy schedule"):
        p = by_name[name]
        print(f"  {name}: id={p['id']} slug={p.get('slug')} grounded={p.get('is_grounded')}")


if __name__ == "__main__":
    main()
