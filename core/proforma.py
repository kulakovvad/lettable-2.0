"""Proforma: rent roll, totals, WAULT, rent psf and gross value, each cell with its provenance.

`compute_cells` and `select_facts` are pure. `build` with a doc_id reads current facts from the
store, writes a new proforma version and its cells.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

from core.models import Cell, Fact, ProformaVersion, valuation_date as default_valuation_date
from evalq.matching import normalize_unit_id

HUMAN_EXTRACTORS = frozenset({"manual", "grok-bot"})
MARKET_EXTRACTORS = frozenset({"rca", "fallback"})
EXTRACTOR_PRIORITY = {"v7go": 0, "grok": 1, "grok-b": 2}
DAYS_PER_YEAR = 365.25
TEXT_FIELDS = ("tenant", "lease_expiry", "break_date")
NUMBER_FIELDS = (("passing_rent", "GBP/yr"), ("area_sqft", "sqft"))
SNAPSHOT_RE = re.compile(r"snapshot:(\S+)")

ExitYield = dict[str, Any]
FactKey = tuple[str | None, str]


def is_human(fact: Fact) -> bool:
    return fact.extractor in HUMAN_EXTRACTORS


def is_eligible(fact: Fact) -> bool:
    """Machine facts need auto routing plus a verbatim quote and a page; human facts need a reason."""
    if fact.value is None or fact.extractor in MARKET_EXTRACTORS:
        return False
    if is_human(fact):
        return bool((fact.reason or "").strip())
    return (
        fact.review_status == "auto"
        and bool(fact.quote)
        and fact.page is not None
        and fact.grounded is not False
    )


def unit_key(unit_id: str | None) -> str | None:
    """Normalized unit id; None stays None for property-level facts."""
    return None if unit_id is None else normalize_unit_id(unit_id)


def _unit_sort_key(unit_id: str | None) -> tuple[Any, ...]:
    parts = re.split(r"(\d+)", unit_id or "")
    return tuple(int(p) if p.isdigit() else p.lower() for p in parts)


def unit_display_names(facts: list[Fact]) -> dict[str | None, str]:
    """Normalized unit id -> the raw id shown in cell names (from the highest-priority extractor)."""
    out: dict[str | None, tuple[int, str]] = {}
    for f in facts:
        if f.unit_id is None:
            continue
        key = unit_key(f.unit_id)
        rank = (EXTRACTOR_PRIORITY.get(f.extractor, 9), f.unit_id)
        if key not in out or rank < out[key]:
            out[key] = rank
    return {k: v[1] for k, v in out.items()}


def select_facts(facts: list[Fact]) -> dict[FactKey, Fact]:
    """One fact per (normalized unit, field): latest human head > auto v7go > auto grok.

    Superseded facts and facts in review are skipped.
    """
    superseded = {f.supersedes_id for f in facts if f.supersedes_id}
    best: dict[FactKey, tuple[tuple[Any, ...], Fact]] = {}
    for f in facts:
        if f.id in superseded or not is_eligible(f):
            continue
        key = (unit_key(f.unit_id), f.field)
        if is_human(f):
            rank: tuple[Any, ...] = (0, -f.created_at.timestamp())
        else:
            rank = (1, EXTRACTOR_PRIORITY.get(f.extractor, 9), -f.created_at.timestamp())
        if key not in best or rank < best[key][0]:
            best[key] = (rank, f)
    return {k: v[1] for k, v in best.items()}


def _as_date(v: Any) -> date | None:
    if isinstance(v, date):
        return v
    if isinstance(v, str):
        try:
            return date.fromisoformat(v[:10])
        except ValueError:
            return None
    return None


def _years(d: date, vd: date) -> float:
    return round((d - vd).days / DAYS_PER_YEAR, 4)


def _yield_pct(v: Any) -> float | None:
    """Exit yield as a percentage; values <= 1 are read as fractions (0.056 -> 5.6)."""
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    if x <= 0:
        return None
    return round(x * 100, 6) if x <= 1 else x


def compute_cells(
    doc_id: str,
    facts: list[Fact],
    valuation_date: date | str | None = None,
    exit_yield: ExitYield | None = None,
    version_id: str = "draft",
) -> list[Cell]:
    vd = _as_date(valuation_date) if valuation_date is not None else default_valuation_date()
    if vd is None:
        raise ValueError(f"bad valuation_date {valuation_date!r}")
    doc_facts = [f for f in facts if f.doc_id == doc_id]
    selected = select_facts(doc_facts)
    names = unit_display_names(doc_facts)
    out: list[Cell] = []

    def add(name: str, formula: str, value: Any, unit: str | None = None,
            fact_ids: list[str] | None = None, cell_ids: list[str] | None = None,
            author: str | None = None, reason: str | None = None) -> Cell:
        c = Cell(version_id=version_id, name=name, formula=formula, value=value, unit=unit,
                 input_fact_ids=fact_ids or [], input_cell_ids=cell_ids or [],
                 author=author, reason=reason)
        out.append(c)
        return c

    vd_cell = add("valuation_date", "assumption", vd.isoformat(), "date",
                  author="config:VALUATION_DATE", reason="valuation date assumption")

    unit_keys = sorted({k[0] for k in selected if k[0] is not None},
                       key=lambda k: _unit_sort_key(names.get(k, k)))
    rent: dict[str, Cell] = {}
    area: dict[str, Cell] = {}
    to_expiry: dict[str, Cell] = {}
    to_break: dict[str, Cell] = {}

    for uk in unit_keys:
        u = names.get(uk, uk or "")
        text_cells: dict[str, Cell] = {}
        for field in TEXT_FIELDS:
            f = selected.get((uk, field))
            if f is not None:
                text_cells[field] = add(f"{field}[{u}]", "fact", f.value,
                                        "date" if field != "tenant" else None, [f.id])
        for field, unit in NUMBER_FIELDS:
            f = selected.get((uk, field))
            if f is None or not isinstance(f.value, (int, float)):
                continue
            c = add(f"{field}[{u}]", "fact", int(f.value), unit, [f.id])
            (rent if field == "passing_rent" else area)[u] = c

        exp_cell = text_cells.get("lease_expiry")
        expiry = _as_date(exp_cell.value) if exp_cell else None
        if exp_cell is not None and expiry is not None:
            to_expiry[u] = add(f"years_to_expiry[{u}]",
                               f"max(0, lease_expiry[{u}] - valuation_date) / {DAYS_PER_YEAR} days",
                               max(0.0, _years(expiry, vd)), "years",
                               cell_ids=[exp_cell.id, vd_cell.id])

        brk_cell = text_cells.get("break_date")
        brk = _as_date(brk_cell.value) if brk_cell else None
        if brk_cell is not None and brk is not None and brk > vd and (expiry is None or brk < expiry):
            to_break[u] = add(f"years_to_break[{u}]",
                              f"(break_date[{u}] - valuation_date) / {DAYS_PER_YEAR} days",
                              _years(brk, vd), "years", cell_ids=[brk_cell.id, vd_cell.id])
        elif u in to_expiry:
            if brk_cell is not None and brk is not None and brk <= vd:
                formula = f"years_to_expiry[{u}] (break {brk.isoformat()} already passed)"
                inputs = [to_expiry[u].id, brk_cell.id]
            else:
                formula = f"years_to_expiry[{u}] (no future break)"
                inputs = [to_expiry[u].id]
            to_break[u] = add(f"years_to_break[{u}]", formula, to_expiry[u].value, "years",
                              cell_ids=inputs)

    total_rent = add("total_rent", "sum(passing_rent[*])",
                     sum(c.value for c in rent.values()) if rent else None, "GBP/yr",
                     cell_ids=[c.id for c in rent.values()])
    total_area = add("total_area", "sum(area_sqft[*])",
                     sum(c.value for c in area.values()) if area else None, "sqft",
                     cell_ids=[c.id for c in area.values()])
    psf = (round(total_rent.value / total_area.value, 2)
           if total_rent.value is not None and total_area.value else None)
    add("rent_psf", "total_rent / total_area", psf, "GBP/sqft/yr",
        cell_ids=[total_rent.id, total_area.id])

    for name, years in (("wault_expiry", to_expiry), ("wault_break", to_break)):
        label = "years_to_expiry" if name == "wault_expiry" else "years_to_break"
        units = [u for u in years if u in rent and rent[u].value > 0]
        skipped = [u for u in rent if rent[u].value > 0 and u not in years]
        weight = sum(rent[u].value for u in units)
        value = (round(sum(rent[u].value * years[u].value for u in units) / weight, 4)
                 if weight > 0 else None)
        formula = f"sum(passing_rent[u] * {label}[u]) / sum(passing_rent[u]) over units with rent > 0"
        if skipped:
            left_out = sum(rent[u].value for u in skipped)
            formula += (f"; excluded, no verified lease_expiry: {', '.join(skipped)} "
                        f"(£{left_out:,} of £{left_out + weight:,} rent)")
        add(name, formula, value, "years",
            cell_ids=[c for u in units for c in (rent[u].id, years[u].id)])

    pct = _yield_pct(exit_yield.get("value")) if exit_yield else None
    if exit_yield and pct is not None:
        snapshot_id = exit_yield.get("snapshot_id")
        source_fact_id = exit_yield.get("source_fact_id")
        formula = f"assumption (snapshot:{snapshot_id})" if snapshot_id else "assumption"
        ey_cell = add("exit_yield", formula, pct, "pct",
                      fact_ids=[source_fact_id] if source_fact_id else [],
                      author=exit_yield.get("author") or "unknown",
                      reason=exit_yield.get("reason"))
        gross = round(total_rent.value / (pct / 100)) if total_rent.value is not None else None
        add("value", "total_rent / (exit_yield / 100); gross value, purchaser's costs not deducted",
            gross, "GBP", cell_ids=[total_rent.id, ey_cell.id])
    else:
        add("value", "total_rent / (exit_yield / 100); no exit yield available", None, "GBP",
            cell_ids=[total_rent.id])
    return out


def exit_yield_from_cells(cells: list[Cell]) -> ExitYield | None:
    """Recover the exit_yield assumption of a previous version so a recompute keeps it."""
    for c in cells:
        if c.name == "exit_yield" and c.value is not None:
            m = SNAPSHOT_RE.search(c.formula)
            return {
                "value": c.value,
                "snapshot_id": m.group(1).rstrip(")") if m else None,
                "source_fact_id": c.input_fact_ids[0] if c.input_fact_ids else None,
                "author": c.author,
                "reason": c.reason,
            }
    return None


def _market_exit_yield(doc_id: str, facts: list[Fact]) -> ExitYield | None:
    property_type = next((f.value for f in facts if f.field == "property_type" and f.value), None)
    try:
        from market import get_exit_yield  # type: ignore[attr-defined]

        return get_exit_yield(doc_id, property_type)
    except Exception:
        return None


def build_version(
    doc_id: str,
    author: str,
    reason: str | None,
    trigger_fact_id: str | None = None,
    exit_yield: ExitYield | None = None,
    valuation_date: date | str | None = None,
) -> ProformaVersion:
    """New proforma version from the current facts of doc_id. Keeps the previous exit yield
    unless a new one is passed; asks `market` only for the first version."""
    from core import store

    facts = store.current_facts(doc_id=doc_id)
    previous = store.versions(doc_id)
    if exit_yield is None:
        exit_yield = (exit_yield_from_cells(store.cells(previous[-1].id)) if previous
                      else _market_exit_yield(doc_id, facts))
    version = ProformaVersion(
        doc_id=doc_id,
        version_no=(previous[-1].version_no + 1) if previous else 1,
        trigger_fact_id=trigger_fact_id,
        author=author,
        reason=reason,
    )
    store.new_version(version)
    store.insert_cells(compute_cells(doc_id, facts, valuation_date, exit_yield, version.id))
    return version


def build(
    doc_id: str | list[Fact],
    author: str | dict[str, Any] | None = None,
    reason: str | None = None,
    trigger_fact_id: str | None = None,
    exit_yield: ExitYield | None = None,
    valuation_date: date | str | None = None,
    version_id: str | None = None,
) -> ProformaVersion | list[Cell]:
    """Two call forms.

    `build(doc_id, author, reason, trigger_fact_id=None, exit_yield=None)` writes a new version
    to the store and returns it.
    `build(facts, assumptions, version_id)` (CONTRACTS.md form) is pure and returns cells;
    assumptions holds `valuation_date` and `exit_yield` (number or dict).
    """
    if isinstance(doc_id, list):
        facts = doc_id
        assumptions = author if isinstance(author, dict) else {}
        ey = assumptions.get("exit_yield")
        if ey is not None and not isinstance(ey, dict):
            ey = {"value": ey, "author": "assumption", "reason": "passed in assumptions"}
        doc = facts[0].doc_id if facts else ""
        return compute_cells(doc, facts, assumptions.get("valuation_date"), ey,
                             version_id or reason or "draft")
    if not isinstance(author, str) or not author.strip():
        raise ValueError("build(doc_id, ...) needs an author")
    return build_version(doc_id, author, reason, trigger_fact_id, exit_yield, valuation_date)
