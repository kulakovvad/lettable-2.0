"""Fixture helpers for core and eval tests: Hidings facts with the statuses Agent 3 would set."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from core.models import Fact, Label

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
DOC = "acuitus-90f42467"

# (unit_id, field) pairs the routing sends to review on the Hidings fixtures.
REVIEW_KEYS = {
    ("Unit 3", "passing_rent"),  # extractors_disagree: 25500 vs 20000
    ("Unit 1", "break_date"),    # missing_in_one: Grok misses the footnote break
    ("Unit 2", "break_date"),    # ungrounded: Grok invents a quote
}


def load_facts(extractor: str) -> list[Fact]:
    name = {"v7go": "facts_v7.json", "grok": "facts_grok.json"}[extractor]
    return [Fact(**f) for f in json.loads((FIXTURES / name).read_text())]


def load_labels() -> list[Label]:
    return [Label(**lb) for lb in json.loads((FIXTURES / "labels.json").read_text())]


def routed(extractor: str) -> list[Fact]:
    out = []
    for f in load_facts(extractor):
        key = (f.unit_id, f.field)
        invented = extractor == "grok" and key == ("Unit 2", "break_date")
        out.append(f.model_copy(update={
            "grounded": not invented,
            "review_status": "review" if key in REVIEW_KEYS else "auto",
            "review_reason": ("ungrounded" if invented else None) if key in REVIEW_KEYS else None,
        }))
    return out


def all_routed() -> list[Fact]:
    return routed("v7go") + routed("grok")


def human(base: Fact, value: object, reason: str = "checked against brochure",
          author: str = "Vadim", minute: int = 30) -> Fact:
    return Fact(doc_id=base.doc_id, unit_id=base.unit_id, field=base.field, value=value,
                unit=base.unit, review_status="auto", extractor="manual", author=author,
                supersedes_id=base.id, reason=reason,
                created_at=datetime(2026, 11, 12, 21, minute, tzinfo=timezone.utc))


def by_id(facts: list[Fact], fact_id: str) -> Fact:
    return next(f for f in facts if f.id == fact_id)


def test_helpers_load_fixtures() -> None:
    assert len(all_routed()) == 72
    assert len(load_labels()) == 36
