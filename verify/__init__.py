"""Verification pipeline: ground -> normalize dates -> date checks -> routing. Pure, no network, no DB."""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from pathlib import Path

from core.models import Fact, valuation_date
from verify.dates import check_unit, normalize_date_facts
from verify.grounding import ground
from verify.routing import route_many


def _date_checked(facts: list[Fact], valuation: date) -> list[Fact]:
    by_unit: dict[tuple[str, str | None], list[Fact]] = defaultdict(list)
    for f in facts:
        by_unit[(f.doc_id, f.unit_id)].append(f)
    checks = {k: check_unit(v, valuation) for k, v in by_unit.items()}
    return [
        f.model_copy(update={"date_check": checks[(f.doc_id, f.unit_id)].get(f.field, "not_applicable")})
        for f in facts
    ]


def verify_doc(
    facts_by_extractor: dict[str, list[Fact]],
    page_texts: list[str],
    pdf_path: str | Path | None = None,
    valuation: date | None = None,
) -> list[Fact]:
    """Routed copies of every extractor's facts for one document."""
    valuation = valuation or valuation_date()
    groups: list[list[Fact]] = []
    for facts in facts_by_extractor.values():
        grounded = [ground(f, page_texts, pdf_path) for f in facts]
        groups.append(_date_checked(normalize_date_facts(grounded), valuation))
    return route_many(groups)
