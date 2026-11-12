from __future__ import annotations

from datetime import date

import pytest

from core import audit
from core.audit import coerce_value, diff_cells, human_fact, review_groups
from core.explain import build_tree, explain_text
from core.models import Snapshot
from core.proforma import compute_cells
from tests.test_core_proforma import EY, confirmed_facts
from tests.test_core_support import DOC, all_routed, by_id, human

VD = date(2026, 11, 12)


def test_correct_fact_without_reason_raises() -> None:
    with pytest.raises(ValueError):
        audit.correct_fact("v7go-unit1-break_date", "2027-01-15", "  ", "Vadim")
    with pytest.raises(ValueError):
        human_fact(by_id(all_routed(), "v7go-unit1-break_date"), "2027-01-15", "", "Vadim")


def test_human_fact_supersedes_and_records_author() -> None:
    base = by_id(all_routed(), "v7go-unit1-break_date")
    f = human_fact(base, "15/01/2027", "tenant served break notice", "Vadim")
    assert f.supersedes_id == base.id
    assert f.value == "2027-01-15"
    assert f.extractor == "manual" and f.author == "Vadim" and f.review_status == "auto"
    assert f.quote is None
    bot = human_fact(base, "2027-01-15", "per agent email", "grok-bot:alice")
    assert bot.extractor == "grok-bot"


def test_coerce_value_uses_uk_dates_and_integer_money() -> None:
    assert coerce_value("lease_start", "05/06/2022") == "2022-06-05"
    assert coerce_value("passing_rent", "£20,000") == 20000
    assert coerce_value("tenant_crn", "00526923") == "00526923"
    with pytest.raises(ValueError):
        coerce_value("break_date", "June-ish")


def test_review_groups_lists_disputes_until_a_person_resolves_them() -> None:
    facts = all_routed()
    groups = review_groups(facts)
    keys = {(g["unit_id"], g["field"]) for g in groups}
    assert keys == {("Unit 3", "passing_rent"), ("Unit 1", "break_date"), ("Unit 2", "break_date")}
    rent = next(g for g in groups if g["field"] == "passing_rent")
    assert {o["value"] for o in rent["options"]} == {25500, 20000}
    assert all(o["quote"] for o in rent["options"])
    resolved = review_groups(confirmed_facts())
    assert {(g["unit_id"], g["field"]) for g in resolved} == {("Unit 2", "break_date")}


def test_diff_shows_changed_cells() -> None:
    facts = confirmed_facts()
    v1 = compute_cells(DOC, facts, VD, EY, "v1")
    moved = human(facts[-1], "2027-01-15", reason="tenant served break notice", minute=45)
    v2 = compute_cells(DOC, facts + [moved], VD, EY, "v2")
    rows = {r["name"]: r for r in diff_cells(v1, v2)}
    assert set(rows) == {"break_date[Unit 1]", "years_to_break[Unit 1]", "wault_break"}
    assert rows["wault_break"]["delta"] < 0
    assert rows["break_date[Unit 1]"]["old"] == "2027-06-01"
    assert rows["break_date[Unit 1]"]["new"] == "2027-01-15"


def test_explain_tree_reaches_quotes_pages_and_snapshot() -> None:
    facts = confirmed_facts()
    cells = compute_cells(DOC, facts, VD, EY, "v1")
    by_cell = {c.id: c for c in cells}
    by_fact = {f.id: f for f in facts}
    snap = Snapshot(id="snap123", provider="rca", query_sql="select ...", row_count=12,
                    result_sha256="ab" * 32)
    value = next(c for c in cells if c.name == "value")
    tree = build_tree(value, by_cell, by_fact.get, {"snap123": snap}.get)
    assert tree["kind"] == "cell" and tree["value"] == round(110800 / 0.056)

    def walk(n: dict) -> list[dict]:
        return [n] + [x for ch in n["children"] for x in walk(ch)]

    nodes = walk(tree)
    facts_seen = [n for n in nodes if n["kind"] == "fact"]
    assert any(n["id"] == "v7go-unit1-passing_rent" and n["page"] == 3 and n["quote"] == "£44,500"
               for n in facts_seen)
    assert any(n["kind"] == "fact" and n["extractor"] == "manual" and n["reason"]
               for n in facts_seen)
    assert any(n["kind"] == "snapshot" for n in nodes)
    text = explain_text(tree)
    assert "v7go-unit1-passing_rent" in text and "page 3" in text
    assert "total_rent / (exit_yield / 100)" in text
    assert "rca@snap123" in text
