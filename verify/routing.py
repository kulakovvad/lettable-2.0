"""Routing: a field goes to the proforma automatically only when every check passes on every side."""

from __future__ import annotations

from collections import defaultdict

from core.models import Fact, ReviewReason
from verify.dates import check_unit
from verify.grounding import number_in_quote
from verify.units import match_units, values_equal

REASON_PRIORITY: tuple[ReviewReason, ...] = (
    "ungrounded",
    "date_inconsistent",
    "number_not_in_quote",
    "missing_in_one",
    "extractors_disagree",
)

Key = tuple[str, str | None, str]


def _with_date_checks(facts: list[Fact]) -> list[Fact]:
    """Fill in date_check where the caller has not run verify.dates yet."""
    if all(f.date_check is not None for f in facts):
        return facts
    by_unit: dict[tuple[str, str | None], list[Fact]] = defaultdict(list)
    for f in facts:
        by_unit[(f.doc_id, f.unit_id)].append(f)
    checks = {k: check_unit(v) for k, v in by_unit.items()}
    return [
        f if f.date_check is not None
        else f.model_copy(update={"date_check": checks[(f.doc_id, f.unit_id)].get(f.field, "not_applicable")})
        for f in facts
    ]


def _fact_problems(f: Fact) -> set[ReviewReason]:
    problems: set[ReviewReason] = set()
    if f.grounded is not True:
        problems.add("ungrounded")
    if f.date_check == "inconsistent":
        problems.add("date_inconsistent")
    if not number_in_quote(f):
        problems.add("number_not_in_quote")
    return problems


UnitMap = dict[tuple[str, str | None], str | None]


def _canonical_units(groups: list[list[Fact]]) -> list[UnitMap]:
    """Per group, (doc_id, unit_id) -> canonical unit id (the first group that mentioned the unit)."""
    maps: list[UnitMap] = [{} for _ in groups]
    for doc in {f.doc_id for g in groups for f in g}:
        canon: list[str | None] = []
        for i, g in enumerate(groups):
            ids = list(dict.fromkeys(f.unit_id for f in g if f.doc_id == doc))
            matched = match_units(ids, canon)
            for uid in ids:
                if uid not in matched:
                    canon.append(uid)
                maps[i][(doc, uid)] = matched.get(uid, uid)
    return maps


def route_many(groups: list[list[Fact]]) -> list[Fact]:
    """N-way routing over one fact list per extractor. Returns routed copies of every input fact."""
    groups = [_with_date_checks(list(g)) for g in groups]
    unit_maps = _canonical_units(groups)
    keyed: dict[Key, list[list[Fact]]] = defaultdict(lambda: [[] for _ in groups])
    for i, g in enumerate(groups):
        for f in g:
            unit = unit_maps[i][(f.doc_id, f.unit_id)]
            keyed[(f.doc_id, unit, f.field)][i].append(f)

    out: list[Fact] = []
    for (_, _, field), sides in keyed.items():
        facts = [f for side in sides for f in side]
        shared: set[ReviewReason] = set()
        if any(not side for side in sides):
            shared.add("missing_in_one")
        if any(not values_equal(field, facts[0].value, f.value) for f in facts[1:]):
            shared.add("extractors_disagree")
        own = {id(f): _fact_problems(f) for f in facts}
        pair_reason = _first(shared.union(*own.values()))
        for f in facts:
            reason = (_first(own[id(f)] | shared) or pair_reason) if pair_reason else None
            out.append(f.model_copy(update={"review_status": "review" if reason else "auto",
                                            "review_reason": reason}))
    return out


def _first(reasons: set[ReviewReason]) -> ReviewReason | None:
    return next((r for r in REASON_PRIORITY if r in reasons), None)


def route(facts_a: list[Fact], facts_b: list[Fact]) -> list[Fact]:
    """Pair facts by (doc_id, unit, field) and set review_status / review_reason on copies of both sides.

    auto only if every fact on both sides is grounded, agrees, passes the date check and shows the
    value in its quote; otherwise every fact of the pair goes to review. Each fact gets the first
    reason by priority among its own problems and the pair's (missing, disagree), falling back to the
    pair's top reason: ungrounded > date_inconsistent > number_not_in_quote > missing_in_one >
    extractors_disagree.
    """
    return route_many([facts_a, facts_b])
