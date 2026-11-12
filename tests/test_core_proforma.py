from __future__ import annotations

from datetime import date

from core.models import Cell, Fact
from core.proforma import (build, compute_cells, exit_yield_from_cells, is_eligible,
                           select_facts, unit_key)
from tests.test_core_support import DOC, all_routed, by_id, human

VD = date(2026, 11, 12)
EY = {"value": 5.6, "source_fact_id": None, "snapshot_id": "snap123", "author": "rca@snap123",
      "reason": "median CAPRATE, UK industrial, 24 months"}


def cells_by_name(cells: list[Cell]) -> dict[str, Cell]:
    return {c.name: c for c in cells}


def confirmed_facts() -> list[Fact]:
    """Routed fixtures plus the two review decisions an analyst makes on Hidings."""
    facts = all_routed()
    facts.append(human(by_id(facts, "grok-unit3-passing_rent"), 20000,
                       reason="stepped rent, £20,000 applies at valuation date"))
    facts.append(human(by_id(facts, "v7go-unit1-break_date"), "2027-06-01",
                       reason="footnote 2 on page 3"))
    return facts


def test_select_facts_prefers_human_then_v7_and_skips_review() -> None:
    facts = all_routed()
    sel = select_facts(facts)
    assert sel[(unit_key("Unit 1"), "passing_rent")].extractor == "v7go"
    assert (unit_key("Unit 3"), "passing_rent") not in sel
    assert (unit_key("Unit 2"), "break_date") not in sel
    sel = select_facts(confirmed_facts())
    assert sel[(unit_key("Unit 3"), "passing_rent")].extractor == "manual"
    assert sel[(unit_key("Unit 3"), "passing_rent")].value == 20000


def test_superseded_human_fact_is_replaced_by_latest() -> None:
    facts = confirmed_facts()
    first = facts[-1]
    second = human(first, "2027-01-15", reason="break notice served", minute=45)
    sel = select_facts(facts + [second])
    assert sel[(unit_key("Unit 1"), "break_date")].id == second.id


def test_machine_fact_without_quote_is_not_eligible() -> None:
    f = by_id(all_routed(), "v7go-unit1-passing_rent")
    assert is_eligible(f)
    assert not is_eligible(f.model_copy(update={"quote": None}))
    assert not is_eligible(f.model_copy(update={"page": None}))
    assert not is_eligible(f.model_copy(update={"grounded": False}))


def test_total_rent_hidings_is_110800_after_review() -> None:
    before = cells_by_name(compute_cells(DOC, all_routed(), VD, EY, "v0"))
    assert before["total_rent"].value == 44500 + 22300 + 24000
    c = cells_by_name(compute_cells(DOC, confirmed_facts(), VD, EY, "v1"))
    assert c["total_rent"].value == 110800
    assert c["passing_rent[Unit 3]"].value == 20000
    assert c["total_area"].value == 4142 + 1562 + 3130 + 2516
    assert c["rent_psf"].value == round(110800 / 11350, 2)
    assert c["value"].value == round(110800 / 0.056)
    assert "gross value" in c["value"].formula
    assert c["total_rent"].formula == "sum(passing_rent[*])"
    assert len(c["total_rent"].input_cell_ids) == 4


def test_provenance_links_point_to_facts_and_cells() -> None:
    cells = compute_cells(DOC, confirmed_facts(), VD, EY, "v1")
    ids = {c.id for c in cells}
    c = cells_by_name(cells)
    assert c["passing_rent[Unit 1]"].input_fact_ids == ["v7go-unit1-passing_rent"]
    for cell in cells:
        assert set(cell.input_cell_ids) <= ids
        if not cell.formula.startswith("assumption"):
            assert cell.input_fact_ids or cell.input_cell_ids, cell.name
    assert c["exit_yield"].author == "rca@snap123"
    assert c["valuation_date"].value == "2026-11-12"


def test_wault_drops_when_unit1_break_moves_closer() -> None:
    facts = confirmed_facts()
    c1 = cells_by_name(compute_cells(DOC, facts, VD, EY, "v1"))
    assert 0 < c1["wault_break"].value < c1["wault_expiry"].value
    moved = human(facts[-1], "2027-01-15", reason="tenant served break notice", minute=45)
    c2 = cells_by_name(compute_cells(DOC, facts + [moved], VD, EY, "v2"))
    assert c2["wault_break"].value < c1["wault_break"].value
    assert c2["wault_expiry"].value == c1["wault_expiry"].value


def test_wault_is_rent_weighted() -> None:
    c = cells_by_name(compute_cells(DOC, confirmed_facts(), VD, EY, "v1"))
    rents = {u: c[f"passing_rent[{u}]"].value for u in ("Unit 1", "Unit 2", "Unit 3", "Unit 4")}
    yrs = {u: c[f"years_to_expiry[{u}]"].value for u in rents}
    expected = sum(rents[u] * yrs[u] for u in rents) / sum(rents.values())
    assert abs(c["wault_expiry"].value - expected) < 1e-3


def test_wault_names_units_left_out() -> None:
    facts = [f.model_copy(update={"review_status": "review"})
             if (f.unit_id, f.field) == ("Unit 2", "lease_expiry") else f
             for f in confirmed_facts()]
    c = cells_by_name(compute_cells(DOC, facts, VD, EY, "v1"))
    assert "years_to_expiry[Unit 2]" not in c
    assert ("excluded, no verified lease_expiry: Unit 2 (£22,300 of £110,800 rent)"
            in c["wault_expiry"].formula)
    assert c["total_rent"].value == 110800


def test_passed_break_is_ignored() -> None:
    facts = confirmed_facts()
    passed = human(by_id(facts, "v7go-unit4-break_date"), "2025-01-01", reason="test")
    c = cells_by_name(compute_cells(DOC, facts + [passed], VD, EY, "v1"))
    assert c["years_to_break[Unit 4]"].value == c["years_to_expiry[Unit 4]"].value
    assert "already passed" in c["years_to_break[Unit 4]"].formula


def test_no_exit_yield_gives_no_value() -> None:
    c = cells_by_name(compute_cells(DOC, confirmed_facts(), VD, None, "v1"))
    assert c["value"].value is None
    assert "exit_yield" not in c


def test_fraction_yield_is_read_as_percent() -> None:
    c = cells_by_name(compute_cells(DOC, confirmed_facts(), VD, {**EY, "value": 0.056}, "v1"))
    assert c["exit_yield"].value == 5.6
    assert c["value"].value == round(110800 / 0.056)


def test_contracts_build_form_is_pure() -> None:
    cells = build(confirmed_facts(), {"valuation_date": "2026-11-12", "exit_yield": 5.6}, "v9")
    assert isinstance(cells, list)
    c = cells_by_name(cells)
    assert c["total_rent"].value == 110800
    assert all(x.version_id == "v9" for x in cells)


def test_exit_yield_survives_recompute() -> None:
    cells = compute_cells(DOC, confirmed_facts(), VD, EY, "v1")
    ey = exit_yield_from_cells(cells)
    assert ey is not None
    assert ey["value"] == 5.6 and ey["snapshot_id"] == "snap123" and ey["author"] == "rca@snap123"
