from __future__ import annotations

import json
from datetime import date

import pytest

from core.proforma import compute_cells
from evalq.score import score
from tests.test_core_support import DOC, FIXTURES, load_facts, load_labels

verify = pytest.importorskip("verify")


def routed_by_verify() -> list:
    pages = [p["text"] for p in json.loads((FIXTURES / "pages_hidings.json").read_text())["pages"]]
    return verify.verify_doc({e: load_facts(e) for e in ("v7go", "grok")}, pages,
                             valuation=date(2026, 11, 12))


def test_verify_then_score_and_proforma_on_hidings() -> None:
    facts = routed_by_verify()
    labels = load_labels()
    v7 = {(r.unit_id, r.field): r.verdict
          for r in score([f for f in facts if f.extractor == "v7go"], labels, "v7go", "r")}
    grok = {(r.unit_id, r.field): r.verdict
            for r in score([f for f in facts if f.extractor == "grok"], labels, "grok", "r")}
    assert v7[("Unit 3", "passing_rent")] == "wrong"
    assert grok[("Unit 1", "break_date")] == "missed"
    assert grok[("Unit 2", "break_date")] == "ungrounded"
    cells = {c.name: c for c in compute_cells(DOC, facts, date(2026, 11, 12), None, "v1")}
    assert cells["total_rent"].value == 44500 + 22300 + 24000
    assert "passing_rent[Unit 3]" not in cells
